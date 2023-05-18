#!/usr/bin/env python3

import os
import sys
import argparse
import numpy as np
import math
from tqdm import tqdm
import gc
import copy
from scipy.special import expit

from datetime import datetime

import pymlir
from utils.mlir_parser import MlirParser
from utils.preprocess import preprocess
from calibration.data_selector import DataSelector


SKIP_OPERATION = [
    'top.Input', 'top.Reshape', 'top.Softmax', 'top.Weight', 'top.MaxPool', 'top.Slice', 'top.Tile',
    'top.Permute', 'top.Upsample'
]

LEARNING_WEIGHT_OPERATION = [
    'top.Conv', 'top.MatMul'#
]

def r_show(op, alpha, weight):
    '''
    import matplotlib.pyplot as plt
    shape = alpha.shape
    bins=200
    min_ = np.min(alpha)
    max_ = np.max(alpha)
    w = copy.deepcopy(alpha.reshape(shape[0],-1).clip(min_, max_))
    h_ = np.histogram(w,bins=bins, range=(min_,max_))
    hist = h_[0][:]
    f=plt.figure()
    f.set_figwidth(10)
    f.set_figheight(6)
    plt.plot(hist)
    plt.savefig('./dist/'+op.replace('/','_')+'_weight')
    plt.close()
    h_ = np.histogram(weight,bins=bins, range=(0,1))
    hist = h_[0][:]
    f=plt.figure()
    f.set_figwidth(10)
    f.set_figheight(6)
    plt.plot(hist)
    plt.savefig('./dist/'+op.replace('/','_')+'_alpha')
    plt.close()
    '''
    return

def remote_show(op, alpha, iter):
    '''
    import matplotlib.pyplot as plt
    shape = alpha.shape
    bins=200
    min_,max_ = -3,3
    w = copy.deepcopy(alpha.reshape(shape[0],-1).clip(min_, max_))
    h_ = np.histogram(w,bins=bins, range=(min_,max_))
    hist = h_[0][:]
    f=plt.figure()
    f.set_figwidth(10)
    f.set_figheight(6)
    plt.plot(hist)
    plt.savefig('./dist/'+op.replace('/','_')+'_'+str(int(iter)))
    plt.close()
    rec = np.clip(1.2*(1/(1+np.exp(-1*w))) - 0.1, 0, 1)
    min_,max_ = 0,1
    h_ = np.histogram(rec,bins=bins, range=(min_,max_))
    hist = h_[0][:]
    #hist = h_[0][1:bins-1]
    f=plt.figure()
    f.set_figwidth(10)
    f.set_figheight(6)
    plt.plot(hist)
    plt.savefig('./dist/'+op.replace('/','_')+'_rect_'+str(int(iter)))
    plt.close()
    '''
    return

def quant_requant_active(data, scale, unsigned=False):
    if unsigned:
        d = data/scale*255.0
        dout = [np.round(x) for x in d]
        return np.clip(dout, 0, 255)/255.0 * scale
    else:
        d = data/scale*127.0
        dout = [np.round(x) for x in d]
        return np.clip(dout, -128, 127)/127.0 * scale

def cal_loss(target, ref):
    mse_diff = ((target - ref)**2).mean()
    return mse_diff

class logging:
    def __init__(self, filename = "logging"):
        self.file_name = filename
        self.log_file = open(self.file_name,'w')

    def logging(self, info):
        print(info, file=self.log_file)

    def end(self):
        if self.log_file is not None:
            self.log_file.close()


class learning_inputs:
    def __init__(self, parser, args):
        self.dataset = args.dataset
        self.data_list = args.data_list
        self.batch_size = parser.get_batch_size()
        self.input_num = parser.get_input_num()
        self.num_sample = 0
        self.parser = parser
        self.ref_activations = {}

    def prepare(self, input_num):
        tune_idx = 0
        self.ref_activations[tune_idx] = {}
        input_names = [op.name for op in self.parser.inputs]
        ds = DataSelector(self.dataset, input_num, self.data_list)
        ppa_list = []
        if ds.all_image:
            for i in range(self.input_num):
                ppa = preprocess()
                ppa.load_config(self.parser.get_input_op_by_idx(i))
                ppa_list.append(ppa)
            n = len(ds.data_list) % self.batch_size
            if n != 0:
                for i in range(self.batch_size - n):
                    ds.data_list.append(ds.data_list[-1])
            self.num_sample = len(ds.data_list) // self.batch_size
            batched_idx = 0
            batched_inputs = self.input_num * ['']
            for data in ds.data_list:
                inputs = data.split(',')
                inputs = [s.strip() for s in inputs]
                assert (len(inputs) == self.input_num)
                batched_idx += 1
                for i, input in enumerate(input_names):
                    batched_inputs[i] += '{},'.format(inputs[i])
                    if batched_idx == self.batch_size:
                        x = ppa_list[i].run(batched_inputs[i][:-1])
                        count = self.parser.get_user_count_by_op_name(input)
                        self.ref_activations[tune_idx][input] = [x, count]
                if batched_idx == self.batch_size:
                    tune_idx += 1
                    batched_idx = 0
                    batched_inputs = self.input_num * ['']
                    self.ref_activations[tune_idx] = {}
        elif ds.all_npy:
            self.num_sample = len(ds.data_list)
            self.input_data_buffer = [[] for i in range(self.num_sample)]
            for data in ds.data_list:
                inputs = data.split(',')
                inputs = [s.strip() for s in inputs]
                assert (len(inputs) == self.input_num)
                for name, npy in zip(input_names, inputs):
                    x = np.load(npy)
                    count = self.parser.get_user_count_by_op_name(name)
                    self.ref_activations[tune_idx][name] = [x, count]
                tune_idx += 1
                self.ref_activations[tune_idx] = {}
        elif ds.all_npz:
            self.num_sample = len(ds.data_list)
            self.input_data_buffer = [[] for i in range(self.num_sample)]
            input_names = [op.name for op in self.parser.inputs]
            for data in ds.data_list:
                npz = np.load(data)
                for name in input_names:
                    count = self.parser.get_user_count_by_op_name(name)
                    self.ref_activations[tune_idx][name] = [npz[name], count]
                tune_idx += 1
                self.ref_activations[tune_idx] = {}
        else:
            raise RuntimeError("dataset is incorrect")
        return self.num_sample

class ref_tensors:
    def __init__(self, loopnum, seg):
        self.loopnum = loopnum
        self.ops = {}
        self.dir = './buf/'
        self.len = 0
        self.seg = seg
        self.cnt = {}
        self.tensors = {}
        self.batch_tensors = []
        self.buffer = {}
        self.atime = {}

        if os.path.exists(self.dir):
            if not os.path.isfile(self.dir+'0-0.npy'):
                return
            total = np.ceil(loopnum/seg)
            cnt = 0
            for i in np.arange(total):
                if not os.path.isfile(self.dir+'0-'+str(int(i))+'.npy'):
                    return
                test = np.load(self.dir+'0-'+str(int(i))+'.npy')
                cnt = cnt + test.shape[0]
                del test
            if cnt < loopnum:
                print(
                    f"samples in buf not enough, {cnt} vs {loopnum}, re run!")
                import shutil
                try:
                    shutil.rmtree(self.dir)
                    os.mkdir(self.dir)
                except OSError as e:
                    print("Error: %s - %s." % (e.filename, e.strerror))
            self.len = cnt
        else:
            os.mkdir(self.dir)

    def load(self, op, idx):
        if op in self.ops:
            fname = self.ops[op]
        else:
            self.ops[op] = str(len(self.ops))
            fname = self.ops[op]
        index = op+'+'+str(idx)
        if index not in self.buffer:
            self.buffer[index] = np.load(self.dir+fname+'-'+str(int(idx))+'.npy')
        self.atime[index] = datetime.now()
        if len(self.buffer) > 8:
            t=datetime.now()
            e=''
            for i in self.buffer:
                if self.atime[i] < t:
                    t = self.atime[i]
                    e = i
            del self.buffer[e]
            del self.atime[e]
        return self.buffer[index]


    def save(self, cnt):
        for op in self.ops:
            fname = self.ops[op]
            shape = list(np.expand_dims(self.batch_tensors[0][op], axis=0).shape)
            if (cnt+1) % self.seg == 0:
                shape[0] = self.seg
            else:
                shape[0] = (cnt+1) % self.seg
            buf = np.zeros(tuple(shape), dtype=np.float32)
            for i in np.arange(shape[0]):
                buf[i] = np.expand_dims(self.batch_tensors[i][op], axis=0)
            np.save(self.dir+fname+'-'+str(cnt//self.seg)+'.npy', buf)
        self.batch_tensors = []
        del buf
        gc.collect()

    def add_name(self, ops):
        for op in ops:
            if op in self.ops:
                continue
            else:
                self.ops[op] = str(len(self.ops))

    def infer(self, module, data: dict, input_names: list):
        for name in input_names:
            module.set_tensor(name, data[name][0])
        module.invoke()
        outputs = {}
        for name in module.output_names:
            outputs[name] = module.get_tensor(name)
        return outputs

    def gather_orig_tensors(self, learner, inputs, idx):
        import sys
        net_input = list(inputs.ref_activations[idx].keys())
        outputs = self.infer(learner.module, inputs.ref_activations[idx], net_input)
        tensors = {}
        for name in self.ops:
            tensors[name] = copy.deepcopy(learner.module.get_tensor(name))
        self.batch_tensors.append(tensors)

    def gather(self, learner, inputs):
        self.add_name(learner.module.all_tensor_names)
        self.add_name(learner.parser.get_op_name_list())
        if self.len < learner.num_sample:
            pbar = tqdm(np.arange(learner.num_sample))
            pbar.set_description("Gather ref ")
            for loop in pbar:
                self.gather_orig_tensors(learner, inputs, loop)
                if (loop+1) % self.seg == 0 or ((loop) == (learner.num_sample - 1)):
                    self.save(loop)
                    gc.collect()

    def get(self, op, loop):
        if op in self.ops:
            fname = self.ops[op]
        else:
            print(f'op not exist {op}')
            print(self.ops)
            sys.exit(1)
        idx = loop//self.seg
        data_all = self.load(op, idx)
        return data_all[loop % self.seg]

class LrScheduler:
    def __init__(self, lr, max_iter, mode):
        self.lr = lr
        self.min_lr = lr/10
        self.mode = mode
        self.max_iter = max_iter
        self.scale = 0.1
        self.warm_up = 0.2

    def cal_lr(self, iter):
        if self.mode == 'Fixed':
            return self.lr
        elif self.mode == 'Cosine':
            if iter <= self.max_iter * self.warm_up:
                return self.lr
            else:
                return self.min_lr + 0.5* (self.lr-self.min_lr)*(1.0+np.cos((iter-self.max_iter*self.warm_up)/(self.max_iter-self.max_iter*self.warm_up)*np.pi))
        elif self.mode == 'MultiStep':
            if iter <= self.max_iter * 0.5:
                return self.lr
            elif iter <= self.max_iter*2/3:
                return self.lr * self.scale
            else:
                return self.lr * self.scale * self.scale

class CaliTable:
    def __init__(self, in_table, out_table):
        self.in_table = in_table
        self.out_table = out_table
        self.table = {}
        self.read()

    def read(self):
        textfile = open(self.in_table, 'r')
        for line in textfile:
            if len(line) > 0 and (not line.startswith("#")):
                s = line.split(" ")
                s = [x for x in s if x != '']
                if len(s) != 4:
                    continue
                self.table[s[0]] = [
                    float(s[1]), float(s[2]), float(s[3])]

    def update(self, new_table):
        for op in new_table:
            for op_ in self.table:
                if op_ == op:
                    self.table[op][0] = new_table[op][0]

    def write(self):
        f = open(self.out_table, 'w')
        for op in self.table:
            f.write(
                f'{op}  {self.table[op][0]}  {self.table[op][1]}  {self.table[op][2]}\n')
        f.close()

class LearningWeight:
    class SgdWeightOpt:
        def __init__(self,lr, momentum=0.0,nesterov=False, weight_decay=0.0, support_unsigned = False):
            self.lr = lr
            self.momentum = momentum
            self.nesterov = nesterov
            self.weight_decay = weight_decay
            self.dampening = 0.0
            self.v = {}
            self.grd = {}
            self.loss = {}
            print(
                f'Learning Weight SGD, momentum is {self.momentum} nesterov is {self.nesterov} weight_decay is {self.weight_decay}')
        def cal_alpha(self, iter, alpha, loss, grd, unsigned=False):
            alpha = alpha - self.lr.cal_lr(iter) * grd
            return alpha
        def update_alpha(self, iter, op, alpha, mini_batch, unsigned=False):
            self.grd[op] = self.grd[op]/mini_batch
            self.loss[op] = self.loss[op]/mini_batch
            if self.weight_decay != 0.0:
                self.grd[op] = self.grd[op] + alpha*self.weight_decay
            if self.momentum != 0.0:
                if op in self.v:
                    self.v[op] = self.v[op]*self.momentum + \
                        self.grd[op]*(1.0-self.dampening)
                else:
                    self.v[op] = self.grd[op]
                if self.nesterov:
                    self.grd[op] = self.v[op]*self.momentum + self.grd[op]
                else:
                    self.grd[op] = self.v[op]

            alpha_new = self.cal_alpha(iter, alpha, self.loss[op], self.grd[op], unsigned)
            self.reset_grd_loss(op)
            return alpha_new

        def update_loss(self, op, loss):
            if op in self.loss:
                self.loss[op] = self.loss[op] + loss
            else:
                self.loss[op] = loss
        def update_grd(self, op, grd):
            if op in self.grd:
                self.grd[op] = self.grd[op] + grd
            else:
                self.grd[op] = grd
        def reset_grd_loss(self, op):
            del self.grd[op]
            self.loss[op] = 0.0

    def __init__(self, args):
        self.scales = None
        self.finetune_layers = []
        self.finetune_layer_weights = {}  # layers to fine tune, without skipped
        self.mlir_file = args.mlir_file
        self.module = pymlir.module()
        self.module.load(self.mlir_file)
        self.parser = MlirParser(args.mlir_file)
        self.batch_size = self.parser.get_batch_size()  # batch size of net
        self.input_num = self.parser.get_input_num()  # number of net inputs
        self.mini_batch = args.mini_batch
        self.num_sample = 0
        self.pre_loss = {}
        self.post_loss = {}
        self.loss = {}
        self.grd = {}
        self.opt = None
        self.zeta = 1.1
        self.gamma = -0.1
        self.lam = 1.0
        self.beta_warmup = 0.2
        self.beta_start = 20
        self.beta_end = 2
        self.reg_param = 0.01
        self.orig_weights = {}
        self.weight_file = self.parser.module_weight_file
        self.param_back = {}
        self.weights_scales = {}
        self.alpha = {}
        self.momentum = args.momentum
        self.dampening = 0.0
        self.nesterov = args.nesterov
        self.weight_decay = args.weight_decay
        self.compare_quanted = True
        print(
            f'Learning Weight, momentum is {self.momentum} nesterov is {self.nesterov} weight_decay is {self.weight_decay}')
        self.v = {}
        self.support_unsigned = False
        self.get_finetune_ops()
        self.backup_weights()
        if self.mini_batch <= self.batch_size:
            self.mini_batch = 1
        else:
            self.mini_batch = self.mini_batch // self.batch_size
        w = np.load(self.weight_file, allow_pickle=True)
        for k in w:
            self.param_back[k] = w[k]

    def sigmoid(self, x):
        return expit(x)

    def rec_sig(self, x):
        return np.clip(self.sigmoid(x)*1.2-0.1,0,1)

    def cal_beta(self, iter):
        if iter < self.num_sample*self.beta_warmup:
            return self.beta_start
        else:
            return int(self.beta_end + 0.5 * (self.beta_start - self.beta_end) * (1.0 + np.cos((iter-self.num_sample*self.beta_warmup)/(self.num_sample*(1.0-self.beta_warmup)) * np.pi)))

    def cal_round_loss(self, iter, alpha, beta, reg=0.01):
        if iter < self.num_sample * self.beta_warmup:
            return 0.0
        else:
            rect_alpha = np.clip((self.zeta - self.gamma)*self.sigmoid(alpha) + self.gamma, 0, 1)
            return reg * (1 - np.power(2 * np.abs(rect_alpha-0.5), beta)).sum()

    def cal_grd_signed(self, out, scale):
        # calculate output mse grd with quant grad
        step = np.abs(scale)/127.0
        grd = np.zeros_like(out)
        m = np.round(out/step)
        qmin = np.ones_like(m)*(-128)
        qmax = np.ones_like(m)*(127)
        g = (np.minimum(np.maximum(m, qmin), qmax)*step - out)*2
        grd_u = np.where(m <= -128, -128, 0)*g
        grd_l = np.where(m >= 127, 127, 0)*g
        left = np.where(m > -128, 1, 0) & np.where(m < 127, 1, 0)
        grd_m = (m - out/step)*left
        return grd_m + grd_u + grd_l

    def cal_grd(self, out, scale, unsigned = False):
        return self.cal_grd_signed(out, scale)

    def cal_grdr_signed(self, alpha, beta, iter, reg=0.01):
        if iter < self.num_sample * self.beta_warmup:
            return np.zeros_like(alpha)
        else:
            sig_a = self.sigmoid(alpha)
            rect_a = 2*np.clip(sig_a*1.2-0.1, 0, 1)-1
            pos = np.where(alpha>=0, 1, 0)
            neg = np.where(alpha<0, 1, 0)
            eff = np.where((sig_a*1.2-0.1)>0, 1, 0) * np.where((sig_a*1.2-0.1)<1.0, 1, 0)
            po = np.where((sig_a*1.2-0.1)>=1.0, -1,0).astype(np.float32)
            no = np.where((sig_a*1.2-0.1)<0, 1, 0).astype(np.float32)
            grdp = -2.4*beta*np.power(rect_a, beta-1)*sig_a*(1-sig_a)*pos
            grdn = 2.4*beta*np.power(-rect_a, beta-1)*sig_a*(1-sig_a)*neg
            grd = ((grdp+grdn)*eff + (no + po)*beta)*reg
            return grd

    def cal_grdr(self, alpha, beta, iter, unsigned = False):
        return self.cal_grdr_signed(alpha, beta, iter)

    def get_finetune_ops(self):
        top_ops = {op.name: op for op in self.parser.ops}
        for op in top_ops:
            if top_ops[op].type in LEARNING_WEIGHT_OPERATION:
                if len(top_ops[op].opds) > 1 and top_ops[op].opds[1] in self.module.all_weight_names:
                    self.finetune_layers.append(op)
                    self.finetune_layer_weights[op] = top_ops[op].opds[1]
        loger.logging(f'Learning Weight running on layers and weights: {self.finetune_layers}')

    def backup_weights(self):
        for op in self.finetune_layers:
            self.orig_weights[op] = copy.deepcopy(self.module.get_tensor(self.finetune_layer_weights[op]))
            oc = self.orig_weights[op].shape[0]
            if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
                self.weights_scales[op] = np.max(np.abs(self.orig_weights[op].reshape(oc,-1)), axis=1)
                self.weights_scales[op] = np.where(self.weights_scales[op]>1e-8, self.weights_scales[op], 1e-8)
            elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
                self.weights_scales[op] = np.max(np.abs(self.orig_weights[op]))
                self.weights_scales[op] = np.where(self.weights_scales[op]>1e-8, self.weights_scales[op], 1e-8)
            else:
                print("not support!")
                sys.exit(1)

    def restore_weight(self, op):
        self.module.set_tensor(self.finetune_layer_weights[op], self.orig_weights[op])

    def quant_requant_weight(self, op, hard=False):
        weight_tmp = copy.deepcopy(self.orig_weights[op])
        scales = self.weights_scales[op]/127.0
        shape=weight_tmp.shape
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            weight_tmp = (weight_tmp.reshape(shape[0],-1)/scales[:,None]).reshape(shape)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            weight_tmp = weight_tmp/scales
        else:
            print("not support!")
            sys.exit(1)
        if op not in self.alpha:  # init
            r_show(op, weight_tmp, weight_tmp-np.floor(weight_tmp))
            alpha = weight_tmp - np.floor(weight_tmp)
            alpha = -np.log((self.zeta-self.gamma)/(alpha - self.gamma)-1) # this is where alpha is coming, refer to ppx and aimet
            self.alpha[op] = alpha
        else:
            alpha = self.alpha[op]
        if not hard:
            if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
                weight = (np.clip(np.floor(weight_tmp)+self.rec_sig(alpha),-128,127).reshape(shape[0],-1)*scales[:,None]).reshape(shape)
            elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
                weight = np.clip(np.floor(weight_tmp)+self.rec_sig(alpha),-128,127)*scales
        else:
            if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
                weight = (np.clip(np.floor(weight_tmp)+(alpha>=0).astype(np.float32),-128,127).reshape(shape[0],-1)*scales[:,None]).reshape(shape)
            elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
                weight = np.clip(np.floor(weight_tmp)+(alpha>=0).astype(np.float32),-128,127)*scales
        self.module.set_tensor(self.finetune_layer_weights[op], weight)

    def quant_requant_weight_orig(self, op):
        weight_tmp = copy.deepcopy(self.orig_weights[op])
        scales = self.weights_scales[op]/127.0
        shape=weight_tmp.shape
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            weight_tmp = (weight_tmp.reshape(shape[0],-1)/scales[:,None]).reshape(shape)
        if self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            weight_tmp = weight_tmp/scales
        weight = np.clip(np.round(weight_tmp), -128.0, 127.0)
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            self.module.set_tensor(self.finetune_layer_weights[op], (weight.reshape(shape[0],-1)*scales[:,None]).reshape(shape))
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            self.module.set_tensor(self.finetune_layer_weights[op], weight*scales)
        else:
            print("not support!")
            sys.exit(1)

    def set_op_inputs(self, op, loop):
        pre_ops = self.parser.get_pre_op_by_op_name(op)
        for pop in pre_ops:
            shape = ref_all_tensor.get(pop, loop).shape
            if pop not in self.module.all_tensor_names:
                if self.parser.get_op_type_by_op_name(pop) == 'top.Reshape':
                    pre_pre_ops = self.parser.get_pre_op_by_op_name(pop)
                    pop=pre_pre_ops[0]
                else:
                    print(f"{op} input {pop} not in all tensor list")
                    sys.exit(1)
            d = ref_all_tensor.get(pop, loop).reshape(shape)
            scale = self.scales[pop][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            if scale != 1.0:
                if self.support_unsigned:
                    d = quant_requant_active(d, scale, unsigned)
                else:
                    d = quant_requant_active(d, scale, False)
            self.module.set_tensor(pop, d)

    def op_first_input(self, op, loop):
        pre_ops = self.parser.get_pre_op_by_op_name(op)
        if len(pre_ops) != 1:
            print(f'input num not 1! {op}')
            sys.exit(1)
        pop = pre_ops[0]
        shape = ref_all_tensor.get(pop, loop).shape
        if pop not in self.module.all_tensor_names:
            if self.parser.get_op_type_by_op_name(pop) == 'top.Reshape':
                pre_pre_ops = self.parser.get_pre_op_by_op_name(pop)
                pop=pre_pre_ops[0]
            else:
                print(f"{op} input {pop} not in all tensor list")
                sys.exit(1)
        d = ref_all_tensor.get(pop, loop).reshape(shape)
        scale = self.scales[pop][0]
        unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
        if scale != 1.0:
            if self.support_unsigned:
                d = quant_requant_active(d, scale, unsigned)
            else:
                d = quant_requant_active(d, scale, False)
        return d

    def learning_one(self, op, progress, total):
        loger.logging(f"now to learn {op}")
        pbar_detail = tqdm(np.arange(self.num_sample*3))
        pbar_detail.set_description("Learning Weight, op %s" % op)

        self.quant_requant_weight_orig(op)
        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(
                f"Cal orig loss {loop} [Total Progress: {progress}/{total}]")
            pbar_detail.update()
            self.set_op_inputs(op, loop)
            outputs = self.module.invoke_at(op)
            scale = self.scales[op][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            if self.compare_quanted:
                if self.support_unsigned:
                    outputs[0] = quant_requant_active(outputs[0], scale, unsigned)
                else:
                    outputs[0] = quant_requant_active(outputs[0], scale, False)
            ref = ref_all_tensor.get(op, loop)
            if op in self.pre_loss:
                pre_loss = self.pre_loss[op] + cal_loss(outputs, ref)
                self.pre_loss[op] = pre_loss
            else:
                pre_loss = cal_loss(outputs, ref)
                self.pre_loss[op] = pre_loss

        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(
                f"Learning {loop} [Total Progress: {progress}/{total}]")
            pbar_detail.update()
            self.set_op_inputs(op, loop)
            self.quant_requant_weight(op)
            outputs = self.module.invoke_at(op)
            scale = self.scales[op][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            if self.support_unsigned:
                outputq = quant_requant_active(outputs[0], scale, unsigned)
            else:
                outputq = quant_requant_active(outputs[0], scale, False)
            ref = ref_all_tensor.get(op, loop)
            beta = self.cal_beta(loop)
            loss = cal_loss(outputq, ref)
            loss += self.cal_round_loss(loop, self.alpha[op], beta)
            self.opt.update_loss(op, loss)
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            #now to calculate grd
            grd_dst = self.cal_grd(outputs[0], scale, unsigned)
            if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
                grd_w = self.module.backward_weight_at(op, self.finetune_layer_weights[op], grd_dst)
            elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
                #should consider transpose? matmul with weight has one input
                input = self.op_first_input(op, loop)
                shape = input.shape
                input = input.reshape(-1,shape[-1]).transpose()
                shape = grd_dst.shape
                grd_d = grd_dst.reshape(-1,shape[-1])
                grd_w = np.matmul(input, grd_d)
                grd_w = grd_w/(np.prod(shape)/(shape[-1]*shape[-2]))
            shape = self.alpha[op].shape
            exp_alpha = (1.0/(expit(self.alpha[op])+1e-8))-1.0
            if self.parser.get_op_type_by_op_name(op) == 'top.MatMul' or self.parser.get_op_type_by_op_name(op) == 'top.Conv':
                grd_w1 =1.2*(exp_alpha/np.power(exp_alpha+1, 2))
            else:
                print("not support!")
                sys.exit(1)
            grd_w = grd_w * grd_w1
            grd_r = self.cal_grdr(self.alpha[op], beta, loop, unsigned)
            grd = grd_w + grd_r
            self.opt.update_grd(op, grd)
            if (loop+1) % self.mini_batch == 0:
                self.alpha[op] = self.opt.update_alpha(loop, op, self.alpha[op], self.mini_batch)
                if (loop+1) % 4 == 0:
                    remote_show(op, self.alpha[op], loop)

        self.quant_requant_weight(op, True)
        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(
                f"Comparing {loop} [Total Progress: {progress}/{total}]")
            pbar_detail.update()
            self.set_op_inputs(op, loop)
            self.module.invoke_at(op)
            outputs = self.module.get_tensor(op)
            scale = self.scales[op][0]
            unsigned = self.scales[op][1] >= 0 and self.scales[op][2] >= 0
            if self.compare_quanted:
                if self.support_unsigned:
                    outputs[0] = quant_requant_active(outputs[0], scale, unsigned)
                else:
                    outputs[0] = quant_requant_active(outputs[0], scale, False)
            ref = ref_all_tensor.get(op, loop)
            if op in self.post_loss:
                post_loss = self.post_loss[op] + cal_loss(outputs, ref)
                self.post_loss[op] = post_loss
            else:
                post_loss = cal_loss(outputs, ref)
                self.post_loss[op] = post_loss

        if self.post_loss[op] <= self.pre_loss[op]:
            loger.logging(f'{op} use trained weight {self.post_loss[op]} vs {self.pre_loss[op]}')
            self.update_weight(op)
        else:
            loger.logging(f'{op} do not use learned weight {self.post_loss[op]} vs {self.pre_loss[op]}')

    def adjust_weight(self, op):
        shape = self.orig_weights[op].shape
        w_reshape = self.orig_weights[op].reshape(shape[0],-1)
        adj = np.argmax(self.orig_weights[op].reshape(shape[0],-1),axis=1)
        p = self.alpha[op].reshape(shape[0],-1)
        for i in np.arange(shape[0]):
            if p[i][adj[i]] >= 0 and w_reshape[i][adj[i]] <= 0:
                p[i][adj[i]] = -p[i][adj[i]]
            elif p[i][adj[i]] < 0 and w_reshape[i][adj[i]] > 0:
                p[i][adj[i]] = -p[i][adj[i]]
        if self.parser.get_op_type_by_op_name(op) == 'top.Conv':
            weight = ((np.floor(w_reshape/((self.weights_scales[op]/127.0)[:,None])) + np.where(p>=0, 1.0, 0.0).astype(np.float32)) * ((self.weights_scales[op]/127.0)[:,None])).reshape(shape)
        elif self.parser.get_op_type_by_op_name(op) == 'top.MatMul':
            weight = ((np.floor(w_reshape/(self.weights_scales[op]/127.0)) + np.where(p>=0, 1.0, 0.0).astype(np.float32)) * (self.weights_scales[op]/127.0)).reshape(shape)
        else:
            print("not support!")
            sys.exit(1)
        return weight

    def update_weight(self, op):
        self.param_back[self.finetune_layer_weights[op]] = self.adjust_weight(op)

    def save_weights(self):
        os.rename(self.weight_file, self.weight_file.replace(".npz",".bak.npz"))
        np.savez(self.weight_file, **self.param_back)

    def learning(self):
        import sys
        total = len(self.finetune_layers)
        progress = 0
        for op in self.finetune_layers:
            self.learning_one(op, progress, total)
            progress = progress+1
        self.save_weights()

class LearningScale:
    class SgdScaleOpt:
        def __init__(self,lr, momentum=0.0,nesterov=False, weight_decay=0.0, support_unsigned=False):
            self.lr = lr
            self.momentum = momentum
            self.nesterov = nesterov
            self.weight_decay = weight_decay
            self.dampening = 0.0
            self.v = {}
            self.grd = {}
            self.loss = {}
            self.support_unsigned = support_unsigned
            print(
                f'Learning Scale SGD, momentum is {self.momentum} nesterov is {self.nesterov} weight_decay is {self.weight_decay}')
        def cal_scale(self, iter, scale, loss, grd, unsigned=False):
            if unsigned:
                step = np.abs(scale)/255.0
            else:
                step = np.abs(scale)/127.0

            step = step - self.lr.cal_lr(iter) * grd
            loger.logging(
                f"update scale step {step} grd {grd} loss {loss}")

            if unsigned:
                scale = step*255.0
            else:
                scale = step*127.0
            return scale
        def update_scale(self, iter, op, scale, mini_batch, unsigned=False):
            self.grd[op] = self.grd[op]/mini_batch
            self.loss[op] = self.loss[op]/mini_batch
            if self.weight_decay != 0.0:
                if unsigned:
                    self.grd[op] = self.grd[op] + np.abs(scale)/255.0*self.weight_decay
                else:
                    self.grd[op] = self.grd[op] + np.abs(scale)/127.0*self.weight_decay
            if self.momentum != 0.0:
                if op in self.v:
                    self.v[op] = self.v[op]*self.momentum + \
                        self.grd[op]*(1.0-self.dampening)
                else:
                    self.v[op] = self.grd[op]
                if self.nesterov:
                    self.grd[op] = self.v[op]*self.momentum + self.grd[op]
                else:
                    self.grd[op] = self.v[op]

            if self.support_unsigned:
                scale = self.cal_scale(iter, scale, self.loss[op], self.grd[op], unsigned)
            else:
                scale = self.cal_scale(iter, scale, self.loss[op], self.grd[op], False)
            self.reset_grd_loss(op)
            return scale

        def update_loss(self, op, loss):
            if op in self.loss:
                self.loss[op] = self.loss[op] + loss
            else:
                self.loss[op] = loss
        def update_grd(self, op, grd):
            if op in self.grd:
                self.grd[op] = self.grd[op] + grd
            else:
                self.grd[op] = grd
        def reset_grd_loss(self, op):
            self.grd[op] = 0.0
            self.loss[op] = 0.0

    class AdamScaleOpt:
        def __init__(self, lr, beta1=0.9, beta2=0.999, weight_decay=0.0, support_unsigned=False):
            self.lr = lr
            self.weight_decay = weight_decay
            self.beta1 = beta1
            self.beta2 = beta2
            self.amsgrad = False
            self.steps = {}
            self.eps = 1e-8
            self.exp_avgs = {}
            self.exp_avgs_sqs = {}
            self.max_exp_avgs_sqs = {}
            self.grd = {}
            self.loss = {}
            self.ams_grads = {}
            print(
                f'Learning Scale Adam, weight_decay is {self.weight_decay}')

        def cal_scale(self, scale, delta, unsigned=False):
            if unsigned:
                step = np.abs(scale)/255.0
            else:
                step = np.abs(scale)/127.0

            step = step - delta
            loger.logging(
                f"update scale step {step} step {step}")

            if unsigned:
                scale = step*255.0
            else:
                scale = step*127.0
            return scale

        def update_scale(self, iter, op, scale, mini_batch, unsigned=False):
            self.grd[op] = self.grd[op]/mini_batch
            self.loss[op] = self.loss[op]/mini_batch
            if op in self.steps:
                self.steps[op] = self.steps[op] + 1
            else:
                self.steps[op] = 1
            if self.weight_decay != 0.0:
                if unsigned:
                    self.grd[op] = scale/255.0*self.weight_decay + self.grd[op]
                else:
                    self.grd[op] = scale/127.0*self.weight_decay + self.grd[op]
            bias_correction1 = 1 - self.beta1 ** self.steps[op]
            bias_correction2 = 1 - self.beta2 ** self.steps[op]

            if op in self.exp_avgs:
                self.exp_avgs[op] = self.exp_avgs[op]*self.beta1+self.grd[op]*(1-self.beta1)
                self.exp_avgs_sqs[op] = self.exp_avgs_sqs[op]*self.beta2+(self.grd[op]**2)*(1-self.beta2)
            else:
                self.exp_avgs[op] = 0
                self.exp_avgs_sqs[op] = 0
                self.max_exp_avgs_sqs[op] = 0
            if self.amsgrad:
                self.max_exp_avgs_sqs[op] = np.maximum(self.max_exp_avgs_sqs[op], self.exp_avgs_sqs[op])
                denorm = np.sqrt(self.max_exp_avgs_sqs[op])/np.sqrt(bias_correction2)+self.eps
            else:
                denorm = np.sqrt(self.exp_avgs_sqs[op])/np.sqrt(bias_correction2)+self.eps
            step_size = self.lr.cal_lr(iter) / bias_correction1
            delta = step_size * self.exp_avgs[op]/denorm
            if unsigned:
                scale = (np.abs(scale)/255.0 - delta)*255.0
                loger.logging(
                    f"update scale {scale/255.0} grd {self.grd[op]} delta {delta}")
            else:
                scale = (np.abs(scale)/127.0 - delta)*127.0
                loger.logging(
                    f"update scale {scale/127.0} grd {self.grd[op]} delta {delta}")
            self.reset_grd_loss(op)
            return scale

        def update_loss(self, op, loss):
            if op in self.loss:
                self.loss[op] = self.loss[op] + loss
            else:
                self.loss[op] = loss
        def update_grd(self, op, grd):
            if op in self.grd:
                self.grd[op] = self.grd[op] + grd
            else:
                self.grd[op] = grd
        def reset_grd_loss(self, op):
            self.grd[op] = 0.0
            self.loss[op] = 0.0

    def __init__(self, args):
        self.args = args
        self.mlir_file = args.mlir_file
        self.chip = args.chip
        self.module = pymlir.module()
        self.module.load(self.mlir_file)
        self.parser = MlirParser(args.mlir_file)
        self.batch_size = self.parser.get_batch_size()  # batch size of net
        self.input_num = self.parser.get_input_num()  # number of net inputs
        self.mini_batch = args.mini_batch
        self.num_sample = 0
        self.orig_scales = {}
        self.new_scales = {}
        self.pre_loss = {}
        self.post_loss = {}
        self.support_unsigned = False
        self.opt = None
        self.finetune_layers = []  # layers to fine tune, without skipped
        self.get_finetune_ops()
        if self.mini_batch <= self.batch_size:
            self.mini_batch = 1
        else:
            self.mini_batch = self.mini_batch // self.batch_size

    def get_finetune_ops(self):
        top_ops = {op.name: op for op in self.parser.ops}
        for n in top_ops:
            if top_ops[n].type not in SKIP_OPERATION:
                self.finetune_layers.append(n)
        loger.logging(f'Learning Scale running on layers: {self.finetune_layers}')

    def set_op_inputs(self, op, loop):
        pre_ops = self.parser.get_pre_op_by_op_name(op)
        for pop in pre_ops:
            shape = ref_all_tensor.get(pop, loop).shape
            if pop not in self.module.all_tensor_names:
                if self.parser.get_op_type_by_op_name(pop) == 'top.Reshape':
                    pre_pre_ops = self.parser.get_pre_op_by_op_name(pop)
                    pop=pre_pre_ops[0]
                else:
                    print(f"{op} input {pop} not in all tensor list")
                    sys.exit(1)
            d = ref_all_tensor.get(pop, loop).reshape(shape)
            scale = 1.0
            if pop in self.new_scales:
                scale = self.new_scales[pop][0]
            elif pop in self.orig_scales:
                scale = self.orig_scales[pop][0]
            unsigned = self.orig_scales[op][1] >= 0 and self.orig_scales[op][2] >= 0
            if scale != 1.0:
                if self.support_unsigned:
                    d = quant_requant_active(d, scale, unsigned)
                else:
                    d = quant_requant_active(d, scale, False)
            self.module.set_tensor(pop, d)

    def cal_grdscale_unsigned(self, out):
        return 1.0 / (out.size * 255.0) ** 0.5

    def cal_grdscale_signed(self, out):
        return 1.0 / (out.size * 127.0) ** 0.5

    def cal_grd_unsigned(self, out, scale):
        step = np.abs(scale)/255.0
        grd = 0
        m = np.round(out/step)
        qmin = np.zeros_like(m)
        qmax = np.ones_like(m)*(255)
        g = (np.minimum(np.maximum(m, qmin), qmax)*step - out)*2
        grd = grd + (np.where(m <= 0, 0, 0)*g).sum()
        grd = grd + (np.where(m >= 255, 255, 0)*g).sum()
        left = np.where(m > 0, 1, 0) & np.where(m < 255, 1, 0)
        m = m-out/step
        m = m*left * g
        grd = grd + m.sum()
        return grd

    def cal_grd_signed(self, out, scale):
        step = np.abs(scale)/127.0
        grd = 0
        m = np.round(out/step)
        qmin = np.ones_like(m)*(-128)
        qmax = np.ones_like(m)*(127)
        g = (np.minimum(np.maximum(m, qmin), qmax)*step - out)*2
        grd = grd + (np.where(m <= -128, -128, 0)*g).sum()
        grd = grd + (np.where(m >= 127, 127, 0)*g).sum()
        left = np.where(m > -128, 1, 0) & np.where(m < 127, 1, 0)
        m = m-out/step
        m = m*left * g
        grd = grd + m.sum()
        return grd

    def cal_grd(self, out, scale, use_grdscale=True, unsigned=False):
        grd_funcs = {'unsigned': self.cal_grd_unsigned,
                     'signed': self.cal_grd_signed}
        grdscale_funcs = {'unsigned': self.cal_grdscale_unsigned,
                          'signed': self.cal_grdscale_signed}
        if unsigned:
            grdfunc = grd_funcs['unsigned']
            grdscalefunc = grdscale_funcs['unsigned']
        else:
            grdfunc = grd_funcs['signed']
            grdscalefunc = grdscale_funcs['signed']

        grd = grdfunc(out, scale)
        grd_scale = grdscalefunc(out)

        if use_grdscale:
            return grd*grd_scale
        else:
            return grd

    def learning_one(self, op, progress, total):
        loger.logging(f"now to learn {op} scale")
        pbar_detail = tqdm(np.arange(self.num_sample*3))
        pbar_detail.set_description("Learning Scale, op %s" % op)
        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(
                f"Cal orig loss {loop} [Total Progress: {progress}/{total}]")
            pbar_detail.update()
            self.set_op_inputs(op, loop)
            outputs = self.module.invoke_at(op)
            scale = self.orig_scales[op][0]
            unsigned = self.orig_scales[op][1] >= 0 and self.orig_scales[op][2] >= 0
            if self.support_unsigned:
                outputs[0] = quant_requant_active(outputs[0], scale, unsigned)
            else:
                outputs[0] = quant_requant_active(outputs[0], scale, False)
            ref = ref_all_tensor.get(op, loop)
            if op in self.pre_loss:
                pre_loss = self.pre_loss[op] + cal_loss(outputs, ref)
                self.pre_loss[op] = pre_loss
            else:
                pre_loss = cal_loss(outputs, ref)
                self.pre_loss[op] = pre_loss

        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(
                f"Learning {loop} [Total Progress: {progress}/{total}]")
            pbar_detail.update()
            self.set_op_inputs(op, loop)
            outputs = self.module.invoke_at(op)
            scale = 1.0
            if op in self.new_scales:
                scale = self.new_scales[op][0]
            elif op in self.orig_scales:
                scale = self.orig_scales[op][0]
            unsigned = self.orig_scales[op][1] >= 0 and self.orig_scales[op][2] >= 0
            if scale != 1.0:
                if self.support_unsigned:
                    outputq = quant_requant_active(outputs[0], scale, unsigned)
                else:
                    outputq = quant_requant_active(outputs[0], scale, False)
            else:
                outputq = outputs[0]
            ref = ref_all_tensor.get(op, loop)
            loss = cal_loss(outputq, ref)
            self.opt.update_loss(op, loss)
            unsigned = self.orig_scales[op][1] >= 0 and self.orig_scales[op][2] >= 0
            grd = self.cal_grd(outputs[0], scale, True, unsigned)
            self.opt.update_grd(op, grd)
            if (loop+1) % self.mini_batch == 0:
                scale = self.opt.update_scale(loop, op, scale, self.mini_batch, unsigned)
                if op in self.new_scales:
                    self.new_scales[op][0] = scale
                else:
                    self.new_scales[op] = [scale, 0, 0]
                loger.logging("{} new scale is {:.16f} iter {} batch {}".format(
                    op, scale, loop+1, self.mini_batch))

        for loop in np.arange(self.num_sample):
            pbar_detail.set_postfix_str(
                f"Comparing {loop} [Total Progress: {progress}/{total}]")
            pbar_detail.update()
            self.set_op_inputs(op, loop)
            self.module.invoke_at(op)
            outputs = self.module.get_tensor(op)
            scale = self.new_scales[op][0]
            unsigned = self.orig_scales[op][1] >= 0 and self.orig_scales[op][2] >= 0
            if self.support_unsigned:
                outputs[0] = quant_requant_active(outputs[0], scale, unsigned)
            else:
                outputs[0] = quant_requant_active(outputs[0], scale, False)
            ref = ref_all_tensor.get(op, loop)
            if op in self.post_loss:
                post_loss = self.post_loss[op] + cal_loss(outputs, ref)
                self.post_loss[op] = post_loss
            else:
                post_loss = cal_loss(outputs, ref)
                self.post_loss[op] = post_loss

        if self.post_loss[op] >= self.pre_loss[op] or self.new_scales[op][0] < 0 or self.new_scales[op][0]/self.orig_scales[op][0] > 1.5:
            loger.logging(
                f'abandon backward tune of {op}, old loss: {self.pre_loss[op]}, new loss: {self.post_loss[op]}, old scale {self.orig_scales[op][0]} new scale {self.new_scales[op][0]}')
            del self.new_scales[op]
        else:
            loger.logging(
                f'use tune of {op}, old loss: {self.pre_loss[op]}, new loss: {self.post_loss[op]}, old scale {self.orig_scales[op][0]} new scale {self.new_scales[op][0]}')

    def learning(self):
        import sys
        total = len(self.finetune_layers)
        progress = 0
        for op in self.finetune_layers:
            self.learning_one(op, progress, total)
            progress = progress+1

if __name__ == '__main__':
    print("SOPHGO Toolchain {}".format(pymlir.module().version))
    # yapf: disable
    parser = argparse.ArgumentParser(
        description="Learning the scale for quantization, run after basic quant table")
    parser.add_argument('mlir_file', help='fp32 mlir file')
    parser.add_argument(
        '--dataset', help='dataset path for mix precision searching')
    parser.add_argument(
        "--data_list", help="specify a file with inputs's absolute path for mix precision searching")
    parser.add_argument('--input_num', type=int, default=1000,
                        help='num of inputs for quantization searching')
    parser.add_argument('--data_seg', type=int, default=2000,
                        help='num of samples to buffer data on disk, they will be re-aranged after gather all samples')
    parser.add_argument('--mini_batch', type=int, default=4,
                        help='batch size for learning')
    parser.add_argument('--momentum', type=float, default=0.9,
                        help='momentum of learning')
    parser.add_argument('--nesterov', action='store_true', dest='nesterov',
                        help='use nesterov in learning')
    parser.add_argument('--weight_decay', type=float, default=0.001,
                        help='weight decay in learning')
    parser.add_argument('--lr', type=float, default=0.001,
                        help='learning rate in learning')
    parser.add_argument('--lr_scheduler', type=str,default='Cosine',
                        choices=['Fixed','Cosine','MultiStep'],
                        help='lr scheduler')
    parser.add_argument('--calibration_table', required=True,
                        help='calibration table generated by calibration or tune tool')
    parser.add_argument('--chip', required=False, type=str,default='bm1684x',
                        choices=['bm1684x', 'bm1684', 'cv183x',
                                 'cv182x', 'cv181x', 'cv180x'],
                        help='chip platform name')
    parser.add_argument('--opt', type=str,default='SGD',
                        choices=['SGD','ADAM'],
                        help='Optimizer')
    parser.add_argument('--target', type=str,default='Scale',
                        choices=['Scale','Weight', 'Both'],
                        help='to learn scale or weight or both')
    parser.add_argument('-o', '--output_calibration_table', required=True, default="./new_cali",
                        help='output of calibration table after learning')

    args = parser.parse_args()
    if args.chip != "bm1684x":
        print("only support bm1684x till now!")
        sys.exit(1)
    if args.data_seg > args.input_num:
        args.data_seg = args.input_num
    loger = logging()
    scale_searcher = LearningScale(args)
    cali_table = CaliTable(args.calibration_table, args.output_calibration_table)
    scale_searcher.orig_scales = cali_table.table
    all_inputs = learning_inputs(scale_searcher.parser, args)
    num_sample = all_inputs.prepare(args.input_num)
    scale_searcher.num_sample = num_sample
    scheduler = LrScheduler(args.lr, scale_searcher.num_sample, args.lr_scheduler)
    learn_scale = args.target == "Scale" or args.target == "Both"
    learn_weight = args.target == "Weight" or args.target == "Both"
    print(f'Learning Scale: {learn_scale}; Learning Weight: {learn_weight}')
    if learn_scale:
        if args.opt == 'SGD':
            scale_searcher.opt = scale_searcher.SgdScaleOpt(scheduler, args.momentum, args.nesterov, args.weight_decay)
        else:
            scale_searcher.opt = scale_searcher.AdamScaleOpt(scheduler, 0.9, 0.999, args.weight_decay)
    ref_all_tensor = ref_tensors(scale_searcher.num_sample, args.data_seg)
    ref_all_tensor.gather(scale_searcher, all_inputs)
    del all_inputs
    if learn_scale:
        scale_searcher.learning()
        cali_table.update(scale_searcher.new_scales)
        cali_table.write()
    del scale_searcher
    if learn_weight:
        weight_searcher = LearningWeight(args)
        weight_searcher.scales = cali_table.table
        weight_searcher.num_sample = num_sample
        weight_searcher.opt = weight_searcher.SgdWeightOpt(scheduler, args.momentum, args.nesterov, args.weight_decay)
        weight_searcher.learning()
        del weight_searcher

    loger.end()
