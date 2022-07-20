import torch
import bitsandbytes as bnb

from typing import Union, Tuple, Any, Callable, Iterator, Set, Optional, overload, TypeVar, Mapping, Dict

from torch import Tensor, device, dtype
from torch import nn
from torch.nn.parameter import Parameter
import torch.nn.functional as F

from bitsandbytes.optim import GlobalOptimManager

T = TypeVar('T', bound='torch.nn.Module')

class StableEmbedding(torch.nn.Embedding):
    def __init__(self, num_embeddings: int, embedding_dim: int, padding_idx: Optional[int] = None,
                 max_norm: Optional[float] = None, norm_type: float = 2., scale_grad_by_freq: bool = False,
                 sparse: bool = True, _weight: Optional[Tensor] = None) -> None:
        super(StableEmbedding, self).__init__(num_embeddings, embedding_dim, padding_idx, max_norm, norm_type, scale_grad_by_freq, False, _weight)
        self.norm = torch.nn.LayerNorm(embedding_dim)
        GlobalOptimManager.get_instance().register_parameters(self.weight)
        GlobalOptimManager.get_instance().override_config(self.weight, 'optim_bits', 32)

    def reset_parameters(self) -> None:
        torch.nn.init.xavier_uniform_(self.weight)
        self._fill_padding_idx_with_zero()

    ''' !!! This is a redefinition of _fill_padding_idx_with_zero in torch.nn.Embedding
        to make the Layer compatible with Pytorch < 1.9.
        This means that if this changes in future PyTorch releases this need to change too
        which is cumbersome. However, with this we can ensure compatibility with previous
        PyTorch releases.
    '''
    def _fill_padding_idx_with_zero(self) -> None:
        if self.padding_idx is not None:
            with torch.no_grad():
                self.weight[self.padding_idx].fill_(0)

    def forward(self, input: Tensor) -> Tensor:
        emb = F.embedding(
            input, self.weight, self.padding_idx, self.max_norm,
            self.norm_type, self.scale_grad_by_freq, self.sparse)

        return self.norm(emb)

class Linear8bit(nn.Linear):
    def __init__(self, input_features, output_features, bias=True, quant_type='vector'):
        super(Linear8bit, self).__init__(input_features, output_features, bias)

    def forward(self, x):
        return bnb.nn.functional.linear8bit(x, self.weight, self.bias)


class Int8Params(torch.nn.Parameter):
    def __new__(cls, data=None, requires_grad=True, has_fp16_weights=False):
        cls.has_fp16_weights = has_fp16_weights
        cls.CB = None
        cls.SCB = None
        if data is None:
            data = torch.empty(0)
        return torch.Tensor._make_subclass(cls, data, requires_grad)

    def cuda(self, device):
        if self.has_fp16_weights:
            return super().cuda(device)
        else:
            # we store the 8-bit rows-major weight
            # we convert this weight to the turning/ampere weight during the first inference pass
            B = self.data.contiguous().half().cuda()
            CB, CBt, SCB, SCBt, coo_tensorB = bnb.functional.double_quant(B)
            del CBt
            del SCBt
            self.data = CB
            setattr(self, 'CB', CB)
            setattr(self, 'SCB', SCB)

        return self

    @overload
    def to(self: T, device: Optional[Union[int, device]] = ..., dtype: Optional[Union[dtype, str]] = ...,
           non_blocking: bool = ...) -> T:
        ...

    @overload
    def to(self: T, dtype: Union[dtype, str], non_blocking: bool = ...) -> T:
        ...

    @overload
    def to(self: T, tensor: Tensor, non_blocking: bool = ...) -> T:
        ...

    def to(self, *args, **kwargs):
        device, dtype, non_blocking, convert_to_format = torch._C._nn._parse_to(*args, **kwargs)

        if device is not None and device.type == 'cuda' and self.data.device.type == 'cpu': return self.cuda(device)
        else:
            new_param = Int8Params(super().to(device=device, dtype=dtype, non_blocking=non_blocking), requires_grad=self.requires_grad, has_fp16_weights=self.has_fp16_weights)
            new_param.CB = self.CB
            new_param.SCB = self.SCB

            return new_param



class Linear8bitLt(nn.Linear):
    def __init__(self, input_features, output_features, bias=True, has_fp16_weights=True, threshold=0.0, index=None):
        super(Linear8bitLt, self).__init__(input_features, output_features, bias)
        self.state = bnb.MatmulLtState()
        self.index=index

        self.state.threshold = threshold
        self.state.has_fp16_weights = has_fp16_weights
        if threshold > 0.0 and not has_fp16_weights:
            self.state.use_pool = True

        self.weight = Int8Params(self.weight.data, has_fp16_weights=has_fp16_weights)

    def init_8bit_state(self):
        self.state.CB = self.weight.CB
        self.state.SCB = self.weight.SCB
        self.weight.CB = None
        self.weight.SCB = None

    def forward(self, x):
        self.state.is_training = self.training

        if self.weight.CB is not None: self.init_8bit_state()
        #assert not self.state.has_fp16_weights
        #if not self.state.has_fp16_weights: assert self.state.CB is not None or self.state.CxB is not None

        out = bnb.matmul(x, self.weight, state=self.state)

        if self.bias is not None:
            out += self.bias.unsqueeze(0).expand_as(out)

        if not self.state.has_fp16_weights and self.state.CB is not None:
            # we converted 8-bit row major to turing/ampere format in the first inference pass
            # we no longer need the row-major weight
            del self.state.CB
            self.weight.data = self.state.CxB

        return out

class Linear8bit(nn.Linear):
    def __init__(self, input_features, output_features, bias=True, quant_type='vector', index=None, args=None, sparse_decomp=False):
        super(Linear8bit, self).__init__(input_features, output_features, bias)
        self.quant_type = quant_type
        self.index = index
        self.args = args
        self.iter = 0

    def forward(self, x):
        self.iter += 1
        if self.iter % self.args.clip_freq == 0:
            with torch.no_grad():
                maxval, maxidx = torch.topk(torch.abs(self.weight.flatten()), k=self.args.clip_idx)
                if not dist.is_initialized() or dist.get_rank() == 0:
                    print('clip', maxval[-1].item())
                self.weight.clip_(-maxval[-1], maxval[-1])


        if self.args is not None:
            out = bnb.nn.functional.sparse_decomposed_linear8bit(x, self.weight, self.bias, qval=self.args.sparse_decomp_val, quant_type=self.args.quant_type)
        else:
            out = bnb.nn.functional.linear8bit(x, self.weight, self.bias, quant_type=self.args.quant_type)

        return out
