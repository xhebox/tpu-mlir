include(FetchContent)
include(ExternalProject)

#include(HandleLLVMOptions)
#include(AddLLVM)
#include(TableGen)
#FetchContent_Declare (
#	mlir
#  URL https://github.com/llvm/llvm-project/releases/download/llvmorg-16.0.0/mlir-16.0.0.src.tar.xz
#	URL_HASH MD5=cae4435791c2711af6e9111b913236b4
#	SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/llvm/mlir
#	PATCH_COMMAND  patch -Np1 -i ${CMAKE_CURRENT_SOURCE_DIR}/mlir.patch
#	DOWNLOAD_EXTRACT_TIMESTAMP True
#)
#FetchContent_MakeAvailable(mlir)
#list(APPEND CMAKE_MODULE_PATH "${MLIR_CMAKE_DIR}")
#include(AddMLIR)
set(MLIR_STANDALONE_BUILD ON)
include(MLIRDetectPythonEnv)
mlir_configure_python_dev_packages()
set(BMLIR_BINARY_DIR ${MLIR_BINARY_DIR})
set(BMLIR_SOURCE_DIR ${MLIR_SOURCE_DIR})
set(MLIR_BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(MLIR_SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/mlir)
add_custom_target(LinalgOdsGen)
add_subdirectory(mlir/python)
set(MLIR_BINARY_DIR ${BMLIR_BINARY_DIR})
set(MLIR_SOURCE_DIR ${BMLIR_SOURCE_DIR})

add_subdirectory(cnpy)

install(FILES caffe/lib/libboost_filesystem.so.1.65.1
              caffe/lib/libboost_python3-py36.so.1.65.1
              caffe/lib/libboost_regex.so.1.65.1
              caffe/lib/libboost_system.so.1.65.1
              caffe/lib/libboost_thread.so.1.65.1
              caffe/lib/libgflags.so.2.2
              caffe/lib/libglog.so.0
              caffe/lib/libprotobuf.so.10
              nntoolchain/lib/libbackend_1684.so
              nntoolchain/lib/libbackend_1684x.so
              nntoolchain/lib/libbackend_1686.so
              nntoolchain/lib/libbmlib.so.0
              nntoolchain/lib/libbmlib.so
              nntoolchain/lib/libcmodel_1684.so
              nntoolchain/lib/libcmodel_1684x.so
              nntoolchain/lib/libcmodel_1686.so
              nntoolchain/lib/libcmodel.so
              nntoolchain/lib/libbmrt.so
              nntoolchain/lib/libbmcpu.so
              nntoolchain/lib/libusercpu.so
              nntoolchain/lib/libcpuop.so
              # cvitek
              CV18xx/lib/libcvikernel.so
              CV18xx/lib/libcviruntime.so
              CV18xx/lib/libcvicmodel.so
              CV18xx/lib/libunwind.so.8
        DESTINATION lib)

install(DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/python_packages/mlir_core/mlir DESTINATION python)
install(DIRECTORY caffe/python/caffe DESTINATION python)
