import torch

from .conv_ws import ConvAWS2d
from .dcn import deform_conv

from mmcv.cnn import CONV_LAYERS


@CONV_LAYERS.register_module('SAC', force=True)
class SAConv2d(ConvAWS2d):
    """SAC (Switchable Atrous Convolution)

    This is an implementation of RFP in DetectoRS
    (https://arxiv.org/pdf/2006.02334.pdf).

    Args:
        in_channels (int): Number of channels in the input image
        out_channels (int): Number of channels produced by the convolution
        kernel_size (int or tuple): Size of the convolving kernel
        stride (int or tuple, optional): Stride of the convolution. Default: 1
        padding (int or tuple, optional): Zero-padding added to both sides of
            the input. Default: 0
        padding_mode (string, optional): ``'zeros'``, ``'reflect'``,
            ``'replicate'`` or ``'circular'``. Default: ``'zeros'``
        dilation (int or tuple, optional): Spacing between kernel elements. Default: 1
        groups (int, optional): Number of blocked connections from input
            channels to output channels. Default: 1
        bias (bool, optional): If ``True``, adds a learnable bias to the
            output. Default: ``True``
        use_deform: if ``True``, replace convolution with deformable convolution
    """

    def __init__(self,
                 in_channels,
                 out_channels,
                 kernel_size,
                 stride=1,
                 padding=0,
                 dilation=1,
                 groups=1,
                 bias=True,
                 use_deform=False):
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            groups=groups,
            bias=bias)
        self.use_deform = use_deform
        self.switch = torch.nn.Conv2d(
            self.in_channels,
            1,
            kernel_size=1,
            stride=stride,
            bias=True)
        self.switch.weight.data.fill_(0)
        self.switch.bias.data.fill_(1)
        self.weight_diff = torch.nn.Parameter(torch.Tensor(self.weight.size()))
        self.weight_diff.data.zero_()
        self.pre_context = torch.nn.Conv2d(
            self.in_channels,
            self.in_channels,
            kernel_size=1,
            bias=True)
        self.pre_context.weight.data.fill_(0)
        self.pre_context.bias.data.fill_(0)
        self.post_context = torch.nn.Conv2d(
            self.out_channels,
            self.out_channels,
            kernel_size=1,
            bias=True)
        self.post_context.weight.data.fill_(0)
        self.post_context.bias.data.fill_(0)
        if self.use_deform:
            self.offset_s = torch.nn.Conv2d(
                self.in_channels,
                18,
                kernel_size=3,
                padding=1,
                stride=stride,
                bias=True)
            self.offset_l = torch.nn.Conv2d(
                self.in_channels,
                18,
                kernel_size=3,
                padding=1,
                stride=stride,
                bias=True)
            self.offset_s.weight.data.fill_(0)
            self.offset_s.bias.data.fill_(0)
            self.offset_l.weight.data.fill_(0)
            self.offset_l.bias.data.fill_(0)

    def forward(self, x):
        # pre-context
        avg_x = torch.nn.functional.adaptive_avg_pool2d(x, output_size=1)
        avg_x = self.pre_context(avg_x)
        avg_x = avg_x.expand_as(x)
        x = x + avg_x
        # switch
        avg_x = torch.nn.functional.pad(x, pad=(2, 2, 2, 2), mode="reflect")
        avg_x = torch.nn.functional.avg_pool2d(avg_x, kernel_size=5, stride=1, padding=0)
        switch = self.switch(avg_x)
        # sac
        weight = self._get_weight(self.weight)
        if self.use_deform:
            offset = self.offset_s(avg_x)
            out_s = deform_conv(
                    x,
                    offset,
                    weight,
                    self.stride,
                    self.padding,
                    self.dilation,
                    self.groups,
                    1)
        else:
            out_s = super().conv2d_forward(x, weight)
        ori_p = self.padding
        ori_d = self.dilation
        self.padding = tuple(3 * p for p in self.padding)
        self.dilation = tuple(3 * d for d in self.dilation)
        weight = weight + self.weight_diff
        if self.use_deform:
            offset = self.offset_l(avg_x)
            out_l = deform_conv(
                    x,
                    offset,
                    weight,
                    self.stride,
                    self.padding,
                    self.dilation,
                    self.groups,
                    1)
        else:
            out_l = super().conv2d_forward(x, weight)
        out = switch * out_s + (1 - switch) * out_l
        self.padding = ori_p
        self.dilation = ori_d
        # post-context
        avg_x = torch.nn.functional.adaptive_avg_pool2d(out, output_size=1)
        avg_x = self.post_context(avg_x)
        avg_x = avg_x.expand_as(out)
        out = out + avg_x
        return out
