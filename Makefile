MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
ROOT_DIR := $(patsubst %/,%,$(dir $(MKFILE_PATH)))

CUDA_HOME :=/home/jheuristic/anaconda3/envs/bloom-8bit/
#CUDA_HOME :=/usr/local/cuda-11.5/
GPP:= /usr/bin/g++
NVCC := $(CUDA_HOME)/bin/nvcc
###########################################

CSRC := $(ROOT_DIR)/csrc
BUILD_DIR:= $(ROOT_DIR)/cuda_build

FILES_CUDA := $(CSRC)/ops.cu $(CSRC)/kernels.cu
FILES_CPP := $(CSRC)/pythonInterface.c


CUTLASS :=$(ROOT_DIR)/dependencies/cutlass

INCLUDE :=  -I $(CUDA_HOME)/include -I $(ROOT_DIR)/csrc -I $(CONDA_PREFIX)/include -I $(ROOT_DIR)/dependencies/cub -I $(CUTLASS)/include -I $(CUTLASS)/tools/util/include/ -I $(CUTLASS)/include/cutlass/gemm/kernel

LIB := -L $(CUDA_HOME)/lib64 -lcudart -lcublas -lcublasLt -lcurand -lcusparse -L $(CONDA_PREFIX)/lib

# NVIDIA NVCC compilation flags
#COMPUTE_CAPABILITY := -gencode arch=compute_50,code=sm_50 # Maxwell
#COMPUTE_CAPABILITY += -gencode arch=compute_52,code=sm_52 # Maxwell
#COMPUTE_CAPABILITY := -gencode arch=compute_70,code=sm_70 # Volta
COMPUTE_CAPABILITY := -gencode arch=compute_75,code=sm_75 # Turing
# COMPUTE_CAPABILITY := -gencode arch=compute_86,code=sm_86 # Turing

all: $(ROOT_DIR)/dependencies/cub $(BUILD_DIR) $(CUTLASS)
	echo $(CONDA_PREFIX)
	#$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' --use_fast_math -Xptxas=-v -dc $(FILES_CUDA) $(INCLUDE) $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' --use_fast_math -dc $(FILES_CUDA) $(INCLUDE) $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' -dlink $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o -o $(BUILD_DIR)/link.o 
	$(GPP) -std=c++11 -shared -fPIC $(INCLUDE) $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o $(BUILD_DIR)/link.o $(FILES_CPP) -o ./bitsandbytes/libbitsandbytes.so $(LIB)

cuda92: $(ROOT_DIR)/dependencies/cub $(BUILD_DIR) $(CUTLASS)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' --use_fast_math -Xptxas=-v -dc $(FILES_CUDA) $(INCLUDE) $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' -dlink $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o -o $(BUILD_DIR)/link.o 
	$(GPP) -std=c++11 -shared -fPIC $(INCLUDE) $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o $(BUILD_DIR)/link.o $(FILES_CPP) -o ./bitsandbytes/libbitsandbytes.so $(LIB)

cuda10x: $(ROOT_DIR)/dependencies/cub $(BUILD_DIR) $(CUTLASS)
	$(NVCC) $(COMPUTE_CAPABILITY) -gencode arch=compute_75,code=sm_75 -Xcompiler '-fPIC' --use_fast_math -Xptxas=-v -dc $(FILES_CUDA) $(INCLUDE) $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' -dlink $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o -o $(BUILD_DIR)/link.o 
	$(GPP) -std=c++11 -shared -fPIC $(INCLUDE) $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o $(BUILD_DIR)/link.o $(FILES_CPP) -o ./bitsandbytes/libbitsandbytes.so $(LIB)

cuda110: $(ROOT_DIR)/dependencies/cub $(BUILD_DIR) $(CUTLASS)
	$(NVCC) $(COMPUTE_CAPABILITY) -gencode arch=compute_80,code=sm_80 -Xcompiler '-fPIC' --use_fast_math -Xptxas=-v -dc $(FILES_CUDA) $(INCLUDE) $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' -dlink $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o -o $(BUILD_DIR)/link.o 
	$(GPP) -std=c++11 -shared -fPIC $(INCLUDE) $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o $(BUILD_DIR)/link.o $(FILES_CPP) -o ./bitsandbytes/libbitsandbytes.so $(LIB)

cuda111: $(ROOT_DIR)/dependencies/cub $(BUILD_DIR) $(CUTLASS)
	$(NVCC) $(COMPUTE_CAPABILITY) -gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -Xcompiler '-fPIC' --use_fast_math -Xptxas=-v -dc $(FILES_CUDA) $(INCLUDE) $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' -dlink $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o -o $(BUILD_DIR)/link.o 
	$(GPP) -std=c++11 -shared -fPIC $(INCLUDE) $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o $(BUILD_DIR)/link.o $(FILES_CPP) -o ./bitsandbytes/libbitsandbytes.so $(LIB)

cuda113: $(BUILD_DIR) $(CUTLASS)
	$(NVCC) $(COMPUTE_CAPABILITY) -gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -Xcompiler '-fPIC' --use_fast_math -Xptxas=-v -dc $(FILES_CUDA) -I $(CUDA_HOME)/include -I $(ROOT_DIR)/include -I $(CONDA_PREFIX)/include $(LIB) --output-directory $(BUILD_DIR)
	$(NVCC) $(COMPUTE_CAPABILITY) -Xcompiler '-fPIC' -dlink $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o -o $(BUILD_DIR)/link.o 
	$(GPP) -std=c++11 -shared -fPIC -I $(CUDA_HOME)/include -I $(ROOT_DIR)/include -I $(CONDA_PREFIX)/include $(BUILD_DIR)/ops.o $(BUILD_DIR)/kernels.o $(BUILD_DIR)/link.o $(FILES_CPP) -o ./bitsandbytes/libbitsandbytes.so $(LIB)

$(BUILD_DIR):
	mkdir -p cuda_build
	mkdir -p dependencies

$(ROOT_DIR)/dependencies/cub:
	git clone https://github.com/NVlabs/cub $(ROOT_DIR)/dependencies/cub

$(CUTLASS):
	git clone https://github.com/NVIDIA/cutlass $(CUTLASS)

clean:
	rm cuda_build/* ./bitsandbytes/libbitsandbytes.so
