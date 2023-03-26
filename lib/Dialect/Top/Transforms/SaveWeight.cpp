//===----------------------------------------------------------------------===//
//
// Copyright (C) 2022 Sophgo Technologies Inc.  All rights reserved.
//
// TPU-MLIR is licensed under the 2-Clause BSD License except for the
// third-party components.
//
//===----------------------------------------------------------------------===//

#include "tpu_mlir/Dialect/Top/Transforms/Passes.h"
#include "tpu_mlir/Support/Module.h"

#include "mlir/IR/PatternMatch.h"
#include "mlir/Transforms/GreedyPatternRewriteDriver.h"

#include <set>

using namespace llvm;
using namespace mlir;

namespace tpu_mlir {
namespace top {

class SaveWeightPass : public SaveWeightBase<SaveWeightPass> {
public:
  SaveWeightPass() {}
  void runOnOperation() override {
    auto mOp = getOperation();
    module::removeUnusedOp();
    // check name conflict
    std::set<StringRef> all_names;
    for (auto func : mOp.getOps<FuncOp>()) {
      func.walk([&](Operation *op) {
        if (op->getLoc().dyn_cast<NameLoc>() && !module::isOpInGroup(op)) {
          auto name = module::getName(op);
          if (all_names.find(name) != all_names.end()) {
            op->dump();
            llvm_unreachable("op name conflict");
          }
          all_names.insert(name);
        }
      });
    }
    bool same_name = true;
    std::string file_name;
    if (this->file_name == "") {
      file_name = module::genWeightFileName(same_name);
    } else {
      same_name = false;
      file_name = this->file_name;
    }
    // weight remove unused in npz
    auto dialect = mOp->getContext()->getLoadedDialect("top");
    auto top_dialect = llvm::cast<top::TopDialect>(dialect);
    if (top_dialect->wFile == nullptr) {
      if (same_name) {
        return;
      }
      auto weight_file = module::getWeightFile();
      top_dialect->loadWeightFile(weight_file);
      top_dialect->wFile->save(file_name);
      module::setWeightFile(file_name);
      return;
    }
    if (top_dialect->wFile->changed() == false && same_name) {
      return;
    }
    std::set<StringRef> weight_names;
    for (auto func : mOp.getOps<FuncOp>()) {
      func.walk([&](top::WeightOp op) {
        weight_names.insert(module::getName(op.getOperation()));
      });
    }
    std::set<StringRef> npz_names;
    top_dialect->wFile->getAllNames(npz_names);
    std::set<StringRef> dif_names;
    for (auto name : npz_names) {
      if (weight_names.find(name) == weight_names.end()) {
        dif_names.insert(name);
      }
    }
    for (auto &name : dif_names) {
      top_dialect->wFile->deleteTensor(name);
    }
    if (top_dialect->wFile->changed() == false && same_name) {
      return;
    }
    top_dialect->wFile->save(file_name);
    module::setWeightFile(file_name);
  }
};

std::unique_ptr<OperationPass<ModuleOp>> createSaveWeightPass() {
  return std::make_unique<SaveWeightPass>();
}
} // namespace top
} // namespace tpu_mlir
