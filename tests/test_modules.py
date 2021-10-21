import pytest
import torch

from torch import nn

import bitsandbytes as bnb

class MockArgs(object):
    def __init__(self, initial_data):
        for key in initial_data:
            setattr(self, key, initial_data[key])

def get_args():
    args = MockArgs([])
    args.quant_type = 'vector'
    args.use_8bit_training = 'full'
    return args

class LinearFunction(torch.autograd.Function):

    @staticmethod
    def get_8bit_linear_trimmed(x, stochastic=False, trim_value=3.0):
        round_func = LinearFunction.round_stoachastic if stochastic else torch.round
        norm = math.sqrt(math.pi)/math.sqrt(2.0)
        #std = torch.abs(x).mean()*norm
        std = torch.std(x)
        max1 = std*trim_value
        x = x/max1*127
        x = round_func(x)
        x[x > 127] = 127
        x[x < -127] = -127
        x = x/127*max1

        return x

    def quant(x, quant_type, dim=1):
        if quant_type == 'linear':
            max1 = torch.abs(x).max().float()
            xq = torch.round(x/max1*127).to(torch.int8)
            return xq, max1
        elif quant_type == 'vector':
            max1 = torch.amax(torch.abs(x), dim=dim, keepdim=True)
            xq = torch.round(x/max1*127).to(torch.int8)
            return xq, max1
        elif quant_type == 'min-max':
            maxA = torch.amax(x, dim=dim, keepdim=True).float()
            minA = torch.amin(x, dim=dim, keepdim=True).float()
            scale = (maxA-minA)/2.0
            xq = torch.round(127*(x-minA-scale)/scale).to(torch.int8)
            return xq, (minA.float(), scale.float())
        else: return None

    def dequant(xq, S1, S2, dtype, quant_type):
        if quant_type == 'linear':
            norm = S1*S2/(127*127)
            # double cast needed to prevent overflows
            return (xq.float()*norm).to(dtype)
        elif quant_type == 'vector':
            x = xq.float()
            if len(xq.shape) == 2 and len(S1.shape) == 3: S1 = S1.squeeze(0)
            if len(xq.shape) == 2 and len(S2.shape) == 3: S2 = S2.squeeze(0)
            #print(x.shape, S1.shape, S2.shape)
            if len(S1.shape) == 2:
                x *= S1.t()/127
            else:
                x *= S1/127
            x *= S2/127
            return x.to(dtype)
        else: return None

    def dequant_min_max(xq, A, B, SA, SB, dtype):
        offset = B.float().t().sum(0)*(SA[0]+SA[1])
        x = xq.float()
        if len(xq.shape) == 2 and len(SB.shape) == 3: SB = SB.squeeze(0)
        if len(xq.shape) == 2 and len(SA.shape) == 3: SA = SA.squeeze(0)
        if len(SB.shape) == 2:
            x *= SB.t()/127
        else:
            x *= SB/127
        x *= SA[1]/127
        x +=offset
        return x.to(dtype)


    def get_8bit_linear(x, stochastic=False):
        round_func = LinearFunction.round_stoachastic if stochastic else torch.round
        max1 = torch.abs(x).max()
        x = x/max1*127
        x = round_func(x)/127*max1
        #x = torch.round(x)/128*max1
        return x

    @staticmethod
    def get_8bit_vector_wise(x, dim, stochastic=False):
        round_func = LinearFunction.round_stoachastic if stochastic else torch.round
        max1 = torch.amax(torch.abs(x), dim=dim, keepdim=True)
        max1[max1==0] = 1.0
        x = (x*127)/max1
        x = round_func(x)/127*max1
        return x

    @staticmethod
    def round_stoachastic(x):
        sign = torch.sign(x)
        absx = torch.abs(x)
        decimal = absx-torch.floor(absx)
        rdm = torch.rand_like(decimal)
        return sign*(torch.floor(absx)+(rdm < decimal).to(x.dtype))

    @staticmethod
    def fake_8bit_storage(w, exponent_bits):
        code = bnb.functional.create_dynamic_map(n=exponent_bits).to(w.device)
        absmax, C = bnb.functional.quantize_blockwise(w.data, code=code)
        out = bnb.functional.dequantize_blockwise(absmax, C, code)
        out = out.half()
        w.copy_(out)
        return out

    @staticmethod
    def fake_8bit_storage_quantile(w, args):
        code = bnb.functional.estimate_quantiles(w.data, offset=args.offset)
        #C = bnb.functional.quantize_no_absmax(code, w)
        #out = bnb.functional.dequantize_no_absmax(code, C, out=w.data)
        #print(out)
        #out = out.half()
        code /= torch.max(torch.abs(code))
        absmax, C = bnb.functional.quantize_blockwise(w.data, code=code)
        out = bnb.functional.dequantize_blockwise(absmax, C, code)
        out = out.half()
        w.copy_(out)
        return out

    @staticmethod
    def fake_8bit_storage_stoachstic(w):
        rand = torch.rand(1024, device=w.device)
        absmax, C = bnb.functional.quantize_blockwise(w.data, rand=rand)
        out = bnb.functional.dequantize_blockwise(absmax, C)
        out = out.half()
        w.copy_(out)
        return out

    @staticmethod
    def fake_8bit_storage_with_max(w, topk=8):
        blocked_w = einops.rearrange(w.flatten(), '(h b) -> h b', b=256)
        max_val, idx = torch.sort(torch.abs(blocked_w), dim=1, descending=True)
        idx = idx[:, :topk]
        max_val = max_val[:, :topk]

        mask = torch.zeros_like(blocked_w)
        mask.scatter_(dim=1, index=idx, src=torch.ones_like(max_val))
        mask = mask.bool()

        # 1. zero out max values
        # 2. quantize + dequantize
        # 3. write back max values
        # 4. copy matrix back to weight

        values = blocked_w[mask]
        blocked_w[mask] = 0

        code = bnb.functional.create_dynamic_map()
        code = code.to(w.device)
        absmax, C = bnb.functional.quantize_blockwise(blocked_w.data)
        bnb.functional.dequantize_blockwise(absmax, C, out=blocked_w)

        blocked_w[mask] = values

        unblocked_w = blocked_w.flatten().view(w.shape)

        w.copy_(unblocked_w)
        return unblocked_w


    @staticmethod
    def forward(ctx, x, weight, bias=None, args=None):
        if args.use_8bit_training != 'off':
            weight8, S1 = LinearFunction.quant(weight, args.quant_type, dim=1)
            x8, S2 = LinearFunction.quant(x, args.quant_type, dim=2)
            outputq = bnb.functional.igemm(x8, weight8.t())
            output = LinearFunction.dequant(outputq, S1, S2, x.dtype, args.quant_type)
            #if torch.rand(1) < 0.01:
                #output32 = torch.matmul(x, weight.t())
                #err = torch.abs(output-output32).float()
                #relerr = err/(torch.abs(output32).float()+1e-8)
                #print(f'{err.mean().item():.4f}, {relerr.mean().item():.4f}', args.quant_type, 'forward', proxy)
        else:
            #output = torch.matmul(x, weight.t())
            output = torch.einsum('bsi,oi->bso', x, weight)

        ctx.save_for_backward(x, weight, bias)
        ctx.args = args

        if bias is not None:
            output += bias.unsqueeze(0).expand_as(output)
        return output

    @staticmethod
    def backward(ctx, grad_output):
        x, weight, bias = ctx.saved_tensors
        args = ctx.args
        stochastic = False
        grad_input = grad_weight = grad_bias = None
        if bias is not None and ctx.needs_input_grad[2]: grad_bias = grad_output.sum(0)

        # weight and x are already 8bit
        # -> transform grad_output to 8-bit
        if args.use_8bit_training == 'forward+wgrad':
            grad_output8, S1 = LinearFunction.quant(grad_output, args.quant_type, dim=[0, 1])
            x8, S2 = LinearFunction.quant(x, args.quant_type, dim=[0, 1])
            grad_weight8 = bnb.functional.igemm(grad_output8, x8)
            grad_weight = LinearFunction.dequant(grad_weight8, S1, S2, grad_output.dtype, args.quant_type)

            #grad_weight32 = torch.einsum('bso,bsi->oi', grad_output, x)

            grad_input = grad_output.matmul(weight)
        elif args.use_8bit_training == 'full':
            grad_output8, S1 = LinearFunction.quant(grad_output, args.quant_type, dim=[0, 1])
            x8, S2 = LinearFunction.quant(x, args.quant_type, dim=[0, 1])
            grad_weight8 = torch.zeros_like(weight, dtype=torch.int32)
            bnb.functional.igemm(grad_output8, x8, out=grad_weight8)
            grad_weight = LinearFunction.dequant(grad_weight8, S1, S2, grad_output.dtype, args.quant_type)

            grad_output8, S1 = LinearFunction.quant(grad_output, args.quant_type, dim=2)
            weight8, S3 = LinearFunction.quant(weight, args.quant_type, dim=0)
            grad_input8 = bnb.functional.igemm(grad_output8, weight8)
            grad_input = LinearFunction.dequant(grad_input8, S1, S3, grad_output.dtype, args.quant_type)

        else:
            grad_input = grad_output.matmul(weight)
            grad_weight = torch.einsum('bsi,bso->oi', x, grad_output)

        return grad_input, grad_weight, grad_bias, None

class Linear8bit(nn.Module):
    def __init__(self, input_features, output_features, bias=True, args=None):
        super(Linear8bit, self).__init__()
        self.input_features = input_features
        self.output_features = output_features
        self.args = args

        self.weight = nn.Parameter(torch.empty(output_features, input_features))
        if bias:
            self.bias = nn.Parameter(torch.empty(output_features))
        else:
            self.register_parameter('bias', None)

        torch.nn.init.xavier_uniform_(self.weight)
        if self.bias is not None:
            torch.nn.init.zeros_(self.bias)

    def forward(self, x):
        self.args.training = self.training

        return LinearFunction.apply(x, self.weight, self.bias, self.args)



def test_linear8bit():
    l1 = bnb.nn.Linear8bit(32,64).cuda()
    l2 = Linear8bit(32, 64, args=get_args()).cuda()

    l1.weight.data = l2.weight.data.clone()
    l1.bias.data = l2.bias.data.clone()

    for i in range(100):
        b1 = torch.randn(16, 8, 32, device='cuda')
        t = torch.randn(16, 8, 64, device='cuda')
        b2 = b1.clone()

        o1 = l1(b1)
        o2 = l2(b2)

        torch.testing.assert_allclose(o1, o2)

        loss1 = torch.nn.functional.mse_loss(o1, t)
        loss2 = torch.nn.functional.mse_loss(o2, t)

        loss1.backward()
        loss2.backward()

        torch.testing.assert_allclose(l1.bias.grad, l2.bias.grad, atol=0.001, rtol=0)
        torch.testing.assert_allclose(l1.weight.grad, l2.weight.grad, atol=0.001, rtol=0)
        l1.weight.grad = None
        l2.weight.grad = None
        l1.bias.grad = None
        l2.bias.grad = None
