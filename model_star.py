#!/usr/bin/env python36
# -*- coding: utf-8 -*-
import pickle
import datetime
import math
import numpy as np
import torch
from torch import nn
from torch.nn import Module, Parameter
import torch.nn.functional as F
from tqdm import tqdm
from PositionalEmbedding import LearnablePositionalEncoder

class StarGNN(Module):
    def __init__(self, hidden_size, step=1):
        super(StarGNN, self).__init__()
        self.step = step
        self.hidden_size = hidden_size
        self.input_size = hidden_size * 2
        self.gate_size = 3 * hidden_size
        self.w_ih = Parameter(torch.Tensor(self.gate_size, self.input_size))
        self.w_hh = Parameter(torch.Tensor(self.gate_size, self.hidden_size))
        self.b_ih = Parameter(torch.Tensor(self.gate_size))
        self.b_hh = Parameter(torch.Tensor(self.gate_size))
        self.b_iah = Parameter(torch.Tensor(self.hidden_size))
        self.b_oah = Parameter(torch.Tensor(self.hidden_size))
        
        self.linear_edge_in = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_edge_out = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_edge_f = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        
        self.Wq1 = nn.Linear(self.hidden_size, self.hidden_size)
        self.Wk1 = nn.Linear(self.hidden_size, self.hidden_size)
        self.Wq2 = nn.Linear(self.hidden_size, self.hidden_size)
        self.Wk2 = nn.Linear(self.hidden_size, self.hidden_size)
        self.Wg  = nn.Linear(2 * self.hidden_size, self.hidden_size)
        
        self.reset_parameters()
    
    def reset_parameters(self):
        stdv = 1.0 / math.sqrt(self.hidden_size)
        for weight in self.parameters():
            weight.data.uniform_(-stdv, stdv)

def GNNCell(self, A, hidden):
    
    input_in = torch.matmul(A[:, :, :A.shape[1]], self.linear_edge_in(hidden)) + self.b_iah
    input_out = torch.matmul(A[:, :, A.shape[1]: 2 * A.shape[1]], self.linear_edge_out(hidden)) + self.b_oah
    inputs = torch.cat([input_in, input_out], 2)
    gi = F.linear(inputs, self.w_ih, self.b_ih)
    gh = F.linear(hidden, self.w_hh, self.b_hh)
    i_r, i_i, i_n = gi.chunk(3, 2)
    h_r, h_i, h_n = gh.chunk(3, 2)
    resetgate = torch.sigmoid(i_r + h_r)
    inputgate = torch.sigmoid(i_i + h_i)
    newgate = torch.tanh(i_n + resetgate * h_n)
    hy = newgate + inputgate * (hidden - newgate)
    return hy
    
    def forward(self, A, hidden, mask):
        
        s = torch.sum(hidden, dim=1) / (torch.sum(mask, dim=1) + 1e-6).unsqueeze(-1)
        
        s = s.unsqueeze(1)
        hidden0 = hidden
        for i in range(self.step):
            hidden1 = self.GNNCell(A, hidden)
            
            alpha = self.Wq1(hidden1).bmm(self.Wk1(s).permute(0, 2, 1)) / math.sqrt(self.hidden_size)
            alpha = torch.softmax(alpha, dim=1)
            # alpha = torch.stack([self.Wq1(hidden1[i]).matmul(self.Wk1(s[i]).T) for i in range(len(hidden1))]) / math.sqrt(self.hidden_size)
            assert not torch.isnan(s).any()
            assert not torch.isnan(alpha).any()
            assert not torch.isnan(hidden1).any()
            
            hidden = (1 - alpha) * hidden1 + alpha * s.repeat(1, alpha.size(1), 1)
            #print(hidden[20,0,1], s[20,0,1], alpha[20,0,0])
            #print(torch.where(hidden != hidden))
            
            assert not torch.isnan(hidden).any()
            beta = self.Wq2(hidden).bmm(self.Wk2(s).permute(0, 2, 1)) / math.sqrt(self.hidden_size)
            # beta = torch.stack([self.Wq2(hidden[i]).matmul(self.Wk2(s[i]).T) for i in range(len(hidden))]) / math.sqrt(self.hidden_size)
            mask = mask[:, :beta.size(1)]
            # beta.masked_fill_(~mask.unsqueeze(-1).bool(), float('-inf'))
            beta = torch.softmax(beta, dim=1)
            assert not torch.isnan(beta).any()
            s = torch.sum(beta * hidden, dim=1, keepdim=True)
        g = self.Wg(torch.cat((hidden0, hidden), dim=-1)).sigmoid_()
        assert not torch.isnan(hidden).any()
        hidden = g * hidden0 + (1-g) * hidden
        
        return hidden, s

class StarSessionGraph(Module):
    def __init__(self, opt, n_node):
        super(StarSessionGraph, self).__init__()
        self.hidden_size = opt.hiddenSize
        self.n_node = n_node
        self.batch_size = opt.batchSize
        self.nonhybrid = opt.nonhybrid
        self.num_heads = opt.heads
        self.embedding = nn.Embedding(self.n_node, self.hidden_size)
        self.gnn = StarGNN(self.hidden_size, step=opt.step)
        self.attn = nn.MultiheadAttention(self.hidden_size, 1)
        self.linear_one = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_two = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_three = nn.Linear(self.hidden_size, self.num_heads, bias=False)
        self.linear_four = nn.Linear(self.hidden_size, self.hidden_size)
        self.linear_transform = nn.Linear(self.hidden_size * (self.num_heads+1), self.hidden_size, bias=True)
        self.layernorm1 = nn.LayerNorm(self.hidden_size)
        self.layernorm2 = nn.LayerNorm(self.hidden_size)
        self.layernorm3 = nn.LayerNorm(self.hidden_size)
        self.loss_function = nn.CrossEntropyLoss()
        # self.loss_function = nn.NLLLoss()
        # self.optimizer = torch.optim.Adam(self.parameters(), lr=opt.lr, weight_decay=opt.l2)
        # self.scheduler = torch.optim.lr_scheduler.StepLR(self.optimizer, step_size=opt.lr_dc_step, gamma=opt.lr_dc)
        self.reset_parameters()
    
    def reset_parameters(self):
        # stdv = 1.0 / math.sqrt(self.hidden_size)
        # for weight in self.parameters():
        #     weight.data.uniform_(-stdv, stdv)
        for weight in self.parameters():
            weight.data.normal_(std=0.1)

def compute_scores(self, hidden, s, mask):
    ht = hidden[torch.arange(mask.shape[0]).long(), torch.sum(mask, 1) - 1]  # batch_size x latent_size
    q1 = self.linear_one(ht).view(ht.shape[0], 1, ht.shape[1])  # batch_size x 1 x latent_size
    q2 = self.linear_two(hidden)  # batch_size x seq_length x latent_size
    q3 = self.linear_four(s)
    alpha = self.linear_three(torch.sigmoid(q1 + q2 + q3)).view(len(hidden), -1, self.num_heads)
    # alpha = torch.softmax(alpha, dim=1)
    
    a = torch.sum(alpha * hidden * mask.view(mask.shape[0], -1, 1).float(), 1).view(len(hidden), -1)
        if not self.nonhybrid:
            a = self.linear_transform(torch.cat([a, ht], 1))
    b = self.embedding.weight[1:]  # n_nodes x latent_size
        a = self.layernorm1(a)
        b = self.layernorm2(b)
        scores = 12 * torch.matmul(a, b.transpose(1, 0))
        return scores

def forward(self, inputs, A, mask):
    
    hidden = self.embedding(inputs)
    hidden = self.layernorm3(hidden)
    hidden, s = self.gnn(A, hidden, mask)
    return hidden, s


def trans_to_cuda(variable):
    if torch.cuda.is_available():
        return variable.cuda()
    else:
        return variable


def trans_to_cpu(variable):
    if torch.cuda.is_available():
        return variable.cpu()
    else:
        return variable


def forward(model, i, data):
    alias_inputs, A, items, mask, targets = data.get_slice(i)
    alias_inputs = trans_to_cuda(torch.Tensor(alias_inputs).long())
    items = trans_to_cuda(torch.Tensor(items).long())
    A = trans_to_cuda(torch.Tensor(A).float())
    mask = trans_to_cuda(torch.Tensor(mask).long())
    hidden, s = model(items, A)
    if model.norm:
        seq_shape = list(hidden.size())
        hidden = hidden.view(-1, model.hidden_size)
        norms = torch.norm(hidden, p=2, dim=1)  # l2 norm over session embedding
        hidden = hidden.div(norms.unsqueeze(-1).expand_as(hidden))
        hidden = hidden.view(seq_shape)
    get = lambda i: hidden[i][alias_inputs[i]]
    seq_hidden = torch.stack([get(i) for i in torch.arange(len(alias_inputs)).long()])
    if model.norm:
        seq_shape = list(seq_hidden.size())
        seq_hidden = seq_hidden.view(-1, model.hidden_size)
        norms = torch.norm(seq_hidden, p=2, dim=1)  # l2 norm over session embedding
        seq_hidden = seq_hidden.div(norms.unsqueeze(-1).expand_as(seq_hidden))
        seq_hidden = seq_hidden.view(seq_shape)
    return targets, model.compute_scores(seq_hidden, mask, s)


def train_test(model, train_data, test_data):
    model.scheduler.step()
    print('start training: ', datetime.datetime.now())
    model.train()
    total_loss = 0.0
    slices = train_data.generate_batch(model.batch_size)
    for i, j in tqdm(zip(slices, np.arange(len(slices))), total=len(slices)):
        model.optimizer.zero_grad()
        targets, scores = forward(model, i, train_data)
        targets = trans_to_cuda(torch.Tensor(targets).long())
        loss = model.loss_function(scores, targets - 1)
        loss.backward()
        model.optimizer.step()
        total_loss += loss
        if j % int(len(slices) / 5 + 1) == 0:
            print('[%d/%d] Loss: %.4f' % (j, len(slices), loss.item()))
    print('\tLoss:\t%.3f' % total_loss)

    print('start predicting: ', datetime.datetime.now())
    model.eval()
    hit, mrr, phi = [], [], []
    slices = test_data.generate_batch(model.batch_size)
    for i in slices:
        targets, scores = forward(model, i, test_data)
        sub_scores = scores.topk(20)[1]
        sub_scores = trans_to_cpu(sub_scores).detach().numpy()
        phic = 0
        for score, target, mask in zip(sub_scores, targets, test_data.mask):
            hit.append(np.isin(target - 1, score))
            for s in score:
                phic += model.count_table[s+1]
            phic /= 20
            phi.append(phic)
            if len(np.where(score == target - 1)[0]) == 0:
                mrr.append(0)
            else:
                mrr.append(1 / (np.where(score == target - 1)[0][0] + 1))
    hit = np.mean(hit) * 100
    mrr = np.mean(mrr) * 100
    phi = np.mean(phi)
    return hit, mrr, phi
