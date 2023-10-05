# BSD 3-Clause License
#
# Copyright (c) 2018, Regents of the University of California
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from typing import Any, Dict, Sequence, Optional, Union, Type, Mapping, Tuple, List

import math

from pybag.enum import RoundMode, MinLenMode, PinMode

from bag.util.math import HalfInt
from bag.util.immutable import Param, ImmutableSortedDict
from bag.layout.template import TemplateDB
from bag.layout.routing.base import TrackID, WireArray
from bag.design.database import Module

from xbase.layout.enum import MOSWireType
from xbase.layout.mos.base import MOSBasePlaceInfo, MOSBase
from xbase.layout.mos.placement.data import TilePattern, TileInfoTable, TilePatternElement

from bag3_digital.layout.stdcells.gates import InvChainCore
from bag3_digital.layout.stdcells.memory import FlopCore, LatchCore

from bag3_liberty.util import parse_cdba_name

from ...schematic.demux_1to2 import bag3_digital__demux_1to2
from ...schematic.des_binary_tree import bag3_digital__des_binary_tree
from ...schematic.des_array_binary_tree import bag3_digital__des_array_binary_tree

"""This module contains layout generators for a (binary) tree structure deserializer."""


def update_subblock_sig_locs(subblock_params: Param, updated_sig_locs: Mapping[str, Union[HalfInt, int, float]]) \
        -> Param:
    sig_locs = subblock_params.get('sig_locs', ImmutableSortedDict()).to_yaml()
    sig_locs.update(updated_sig_locs)
    return subblock_params.copy(append=dict(sig_locs=sig_locs))


class Demux1To2(MOSBase):
    """ A 1:2 demux unit cell used in tree structure deserializers. """

    @classmethod
    def get_schematic_class(cls) -> Optional[Type[Module]]:
        return bag3_digital__demux_1to2

    @classmethod
    def get_params_info(cls) -> Mapping[str, str]:
        return dict(
            pinfo='The MOSBasePlaceInfo object.',
            dlatch_params='DLatch parameters',
            in_buf_params='Optional input data buffer parameters. If None, removed. Defaults to None.',
            clk_buf_params='Optional inverter chain parameters to buffer clk and generate clkb. If None, '
                           'clkb is an input pin. If 1 stage, the data latches/FFs use the input clk and generated '
                           'clkb. If > 1 stage, the data latches/FFs use buffered clk and clkb. Defaults to None.',
            use_ff='True to have flip flops on both outputs (resulting in 2 latches on one way and 3 latches on the '
                   'other). False to have 1 latch on one way and 2 latches on the other. Defaults to False.',
            is_big_endian='True for big endian, False for little endian. Defaults to False.',
            export_nets='True to export intermediate nets. Defaults to False',
            ridx_p='pmos row index.',
            ridx_n='nmos row index.',
            sig_locs='Signal track location dictionary.',
            connect_in='True to connect dlatch and dff inputs. Defaults to True',
            vertical_sup='True to have supply unconnected on conn_layer.',
            clk_on_right="True to place clock buffer on right, else left. Defaults to False",
        )

    @classmethod
    def get_default_param_values(cls) -> Mapping[str, Any]:
        return dict(
            in_buf_params=None,
            clk_buf_params=None,
            use_ff=False,
            is_big_endian=False,
            export_nets=False,
            ridx_p=-1,
            ridx_n=0,
            sig_locs={},
            connect_in=True,
            vertical_sup=False,
            clk_on_right=False
        )

    def draw_layout(self) -> None:
        pinfo = MOSBasePlaceInfo.make_place_info(self.grid, self.params['pinfo'])
        self.draw_base(pinfo)

        dlatch_params: Param = self.params['dlatch_params']
        in_buf_params: Optional[Param] = self.params['in_buf_params']
        clk_buf_params: Optional[Param] = self.params['clk_buf_params']
        is_big_endian: bool = self.params['is_big_endian']
        export_nets: bool = self.params['export_nets']
        ridx_p: int = self.params['ridx_p']
        ridx_n: int = self.params['ridx_n']
        sig_locs: Dict[str, Union[HalfInt, int, float]] = self.params['sig_locs']
        connect_in: bool = self.params['connect_in']
        use_ff: bool = self.params['use_ff']
        vertical_sup: bool = self.params['vertical_sup']
        clk_on_right: bool = self.params['clk_on_right']

        has_in_buf = in_buf_params is not None
        has_clk_buf = clk_buf_params is not None

        hm_layer = self.conn_layer + 1
        vm_layer = hm_layer + 1

        # --- Create instances --- #
        shared_params = dict(pinfo=pinfo, ridx_p=ridx_p, ridx_n=ridx_n, vertical_sup=vertical_sup)

        dff_params = dlatch_params.copy(append=dict(**shared_params, seg_ck=0))
        dlatch_params = dlatch_params.copy(append=shared_params)

        pclkb_tidx = self.get_track_index(ridx_p, MOSWireType.G, wire_name='sig', wire_idx=0)
        nclk_tidx = self.get_track_index(ridx_n, MOSWireType.G, wire_name='sig', wire_idx=1)
        nclkb_tidx = self.get_track_index(ridx_n, MOSWireType.G, wire_name='sig', wire_idx=0)
        pclk_tidx = self.get_track_index(ridx_p, MOSWireType.G, wire_name='sig', wire_idx=1)

        sig_locs = {'nclk': nclk_tidx, 'nclkb': nclkb_tidx, 'pclk': pclk_tidx, 'nin': pclkb_tidx, 'pclkb': pclk_tidx,
                    'pout': sig_locs.get('pout', pclkb_tidx)}
        dlatch_params = update_subblock_sig_locs(dlatch_params, sig_locs)
        dff_master: FlopCore = self.new_template(FlopCore, params=dff_params)
        dlatch_master: LatchCore = self.new_template(LatchCore, params=dlatch_params)

        if has_in_buf:
            in_buf_params = in_buf_params.copy(append=shared_params)
            in_buf_master: InvChainCore = self.new_template(InvChainCore, params=in_buf_params)
        else:
            in_buf_master = None

        if has_clk_buf:
            clk_buf_params = clk_buf_params.copy(append=dict(**shared_params, dual_output=True))
            clk_buf_params = update_subblock_sig_locs(clk_buf_params, {'nin0': nclk_tidx, 'nin1': nclkb_tidx})
            clk_buf_master: InvChainCore = self.new_template(InvChainCore, params=clk_buf_params)
        else:
            clk_buf_master = None

        # --- Place instances --- #
        cur_col = 0
        if has_in_buf:
            in_buf_inst = self.add_tile(in_buf_master, 0, cur_col)
            cur_col += in_buf_master.num_cols + self.min_sep_col
        else:
            in_buf_inst = None

        if has_clk_buf and not clk_on_right:
            if clk_buf_master.num_stages > 1:
                coord_delta = self.tr_manager.get_sep(vm_layer, ('sig', 'sig')) * self.grid.get_track_pitch(vm_layer)
                cur_col += (coord_delta / self.sd_pitch).up_even(True).value
            clk_buf_inst = self.add_tile(clk_buf_master, 0, cur_col)
            cur_col += clk_buf_master.num_cols + self.min_sep_col

        dlatch_inst = self.add_tile(dlatch_master, 0, cur_col)
        cur_col += dlatch_master.num_cols + self.min_sep_col
        dff_inst = self.add_tile(dff_master, 0, cur_col)
        cur_col += dff_master.num_cols + self.min_sep_col

        if has_clk_buf and clk_on_right:
            if clk_buf_master.num_stages > 1:
                coord_delta = self.tr_manager.get_sep(vm_layer, ('sig', 'sig')) * self.grid.get_track_pitch(vm_layer)
                cur_col += (coord_delta / self.sd_pitch).up_even(True).value
            clk_buf_inst = self.add_tile(clk_buf_master, 0, cur_col + clk_buf_master.num_cols, flip_lr=True)
            cur_col += clk_buf_master.num_cols + self.min_sep_col
        
        if not has_clk_buf:
            clk_buf_inst = None

        all_insts = [in_buf_inst, clk_buf_inst, dlatch_inst, dff_inst]

        if use_ff:
            out_inst_early = dff_inst
            in_inst_early = dlatch_inst
            in_inst_late = out_inst_late = self.add_tile(dff_master, 0, cur_col)
            all_insts.append(out_inst_late)
        else:
            in_inst_early = out_inst_early = dff_inst
            in_inst_late = out_inst_late = dlatch_inst
        self.set_mos_size()

        # --- Routing --- #
        # Supplies
        if vertical_sup:
            # Conn layer
            vdd_conn, vss_conn = [], []
            for inst in all_insts:
                if not inst:
                    continue
                vdd_conn += inst.get_all_port_pins('VDD', self.conn_layer)
                vss_conn += inst.get_all_port_pins('VSS', self.conn_layer)
            vdd_hm = self.connect_wires(vdd_conn)
            vss_hm = self.connect_wires(vss_conn)
        else:
            vdd_hm = [inst.get_pin('VDD') for inst in all_insts if inst is not None]
            vss_hm = [inst.get_pin('VSS') for inst in all_insts if inst is not None]
            vdd_hm = self.connect_wires(vdd_hm)
            vss_hm = self.connect_wires(vss_hm)

        self.add_pin('VDD', vdd_hm)
        self.add_pin('VSS', vss_hm)

        # Clock signals
        if has_clk_buf:
            if not clk_on_right:
                if clk_buf_master.num_stages == 1:
                    self.connect_wires([clk_buf_inst.get_pin('in'), dlatch_inst.get_pin('nclk')])
                    self.reexport(dlatch_inst.get_port('clk'))
                else:
                    self.connect_to_track_wires(dlatch_inst.get_pin('nclk'), clk_buf_inst.get_pin('out'))
                    self.reexport(dlatch_inst.get_port('clk'), net_name='clk_buf', hide=not export_nets)
                    vm_tid = TrackID(vm_layer, self.grid.coord_to_track(vm_layer, clk_buf_inst.bound_box.xl,
                                                                        RoundMode.GREATER))
                    vm_tid = self.tr_manager.get_next_track_obj(vm_tid, 'sig', 'sig', -1)
                    self.add_pin('clk', self.connect_to_tracks(clk_buf_inst.get_pin('in'), vm_tid,
                                                            min_len_mode=MinLenMode.MIDDLE))
                self.connect_to_track_wires(dlatch_inst.get_pin('nclkb'), clk_buf_inst.get_pin('outb'))
                self.reexport(dlatch_inst.get_port('clkb'), net_name='clkb_buf', hide=not export_nets)
            else:
                if clk_buf_master.num_stages == 1:
                    self.connect_wires([clk_buf_inst.get_pin('in'), dff_inst.get_pin('pclk')])
                    self.reexport(dlatch_inst.get_port('clk'))
                else:
                    self.connect_to_track_wires(dff_inst.get_pin('pclk_s'), clk_buf_inst.get_pin('out'))
                    self.reexport(dff_inst.get_port('clk'), net_name='clk_buf', hide=not export_nets)
                    vm_tid = TrackID(vm_layer, self.grid.coord_to_track(vm_layer, clk_buf_inst.bound_box.xh,
                                                                        RoundMode.LESS))
                    vm_tid = self.tr_manager.get_next_track_obj(vm_tid, 'sig', 'sig', 1)
                    self.add_pin('clk', self.connect_to_tracks(clk_buf_inst.get_pin('in'), vm_tid,
                                                            min_len_mode=MinLenMode.MIDDLE))
                self.connect_to_track_wires(dff_inst.get_pin('nclkb_s'), clk_buf_inst.get_pin('outb'))
                self.reexport(dff_inst.get_port('clkb'), net_name='clkb_buf', hide=not export_nets)
        else:
            self.reexport(dlatch_inst.get_port('clkb'))
            self.reexport(dlatch_inst.get_port('clk'))

        # Share clock routes
        self.connect_wires([dlatch_inst.get_pin('nclkb'), dff_inst.get_pin('nclkb')])
        self.connect_to_track_wires(dlatch_inst.get_pin('pclk'), dff_inst.get_pin('clk'))

        if use_ff:
            self.connect_wires([dff_inst.get_pin('nclkb_s'), out_inst_late.get_pin('nclkb')])
            self.connect_wires([dff_inst.get_pin('pclk_s'), out_inst_late.get_pin('pclk')])
            # FF buffer route
            self.connect_to_track_wires(dlatch_inst.get_pin('out'), dff_inst.get_pin('nin'))

        # Outputs
        self.reexport(out_inst_late.get_port('out'), net_name=f'out<{int(not is_big_endian)}>')
        self.reexport(out_inst_early.get_port('out'), net_name=f'out<{int(is_big_endian)}>')

        # Inputs
        if has_in_buf:
            self.reexport(in_buf_inst.get_port('in'))

        if connect_in:
            in_warrs = [warr for inst in (dlatch_inst, dff_inst) for warr in inst.get_all_port_pins('in')]
            if has_in_buf:
                in_warrs.append(in_buf_inst.get_pin('out'))
            in_hm_tid = self.get_track_id(ridx_n, MOSWireType.G, wire_name='sig', wire_idx=2)
            in_hm = self.connect_to_tracks(in_warrs, in_hm_tid)
            if has_in_buf:
                self.add_pin('in_buf', in_hm, hide=not export_nets)
            else:
                self.add_pin('in', in_hm)
        else:
            in_warrs = []
            vm_w_sig = self.tr_manager.get_width(vm_layer, 'sig')
            for inst in [in_inst_early, in_inst_late]:
                in_hm = inst.get_pin('pin')
                in_coord = inst.get_pin('in')[0].bound_box.xm
                vm_tidx = self.grid.coord_to_track(vm_layer, in_coord, RoundMode.NEAREST)
                vm_tid = TrackID(vm_layer, vm_tidx, width=vm_w_sig)
                in_warrs.append(self.connect_to_tracks(in_hm, vm_tid, min_len_mode=MinLenMode.MIDDLE))
            if has_in_buf:
                in_warrs.append(in_buf_inst.get_pin('out'))
            else:
                self.add_pin('in', in_warrs, connect=True)

        self.sch_params = dict(
            dlatch_params=dlatch_master.sch_params,
            in_buf_params=in_buf_master and in_buf_master.sch_params,
            clk_buf_params=clk_buf_master and clk_buf_master.sch_params,
            is_big_endian=is_big_endian,
            export_nets=export_nets,
            use_ff=use_ff,
        )


class DesBinaryTree(MOSBase):
    """ A single binary tree deserializer.
    To use substrate rows, set logic_tidx, ntap_tidx, and ptap_tidx.
    To use substrate columns, set only logic_tidx=0, and leave ntap_tidx and ptap_tidx to None.
    """
    # TODO: add/implement sig_locs parameter
    # TODO: support multiple rows
    # TODO: support more than 2 horizontal layers used for signal routing.
    # TODO: add optional supply routing to higher layer

    @classmethod
    def get_schematic_class(cls) -> Optional[Type[Module]]:
        return bag3_digital__des_binary_tree

    @classmethod
    def get_params_info(cls) -> Mapping[str, str]:
        return dict(
            pinfo='The MOSBasePlaceInfo object.',
            num_stages='Number of stages. Deserialization ratio is 2^num_stages',
            demux_params='Demux 1:2 parameters',
            use_ff_list='List of booleans mapping whether each demux stage should have flip flops on both outputs '
                        '(refer to Demux1To2 for more info). If a boolean is specified, '
                        'all stages will be set to this boolean. Defaults to False.',
            din_buf_params='Input data buffer parameters. If None, removed. Defaults to None.',
            is_big_endian='True for big endian, False for little endian. Defaults to False.',
            export_nets='True to export intermediate nets. Defaults to False',
            ridx_p='pmos row index.',
            ridx_n='nmos row index.',
            num_sig_hor_layers='Number of horizontal layers to use for signal routing. Defaults to 1.',
            tap_sep_unit = 'Horizontal separation between column taps in number of demux units. Default is ratio // 2.',
            export_unit_sup='True to export demux unit supply pins. Defaults to False.',
            clk_layer='Clock layer',
            logic_tidx='Tile index. Defaults to 0',
            ptap_tidx='ptap tile index. If None, assume no substrate rows are used. Defaults to None',
            ntap_tidx='ntap tile index. If None, assume no substrate rows are used. Defaults to None',
            draw_taps='True to draw substrate taps. Defaults to True',
        )

    @classmethod
    def get_default_param_values(cls) -> Mapping[str, Any]:
        return dict(
            use_ff_list=False,
            din_buf_params=None,
            is_big_endian=False,
            export_nets=False,
            ridx_p=-1,
            ridx_n=0,
            num_sig_hor_layers=1,
            tap_sep_unit=-1,
            export_unit_sup=False,
            clk_layer=None,
            logic_tidx=0,
            ptap_tidx=None,
            ntap_tidx=None,
            draw_taps=True,
        )

    @property
    def num_stages(self) -> int:
        return self.params['num_stages']

    @property
    def ratio(self) -> int:
        return 1 << self.num_stages

    def connect_sig_to_tid(self, warr_list: Union[WireArray, Sequence[WireArray]], tid: TrackID,
                           conn_tid: Optional[TrackID]) -> WireArray:
        """
        Helper function to connect data wire from demux to its horizontal track.

        Parameters
        ----------
        warr_list: Union[WireArray, Sequence[WireArray]]
            The demux wire(s) to connect.
        tid: TrackID
            The track ID to connect warr_list to.
        conn_tid: Optional[TrackID]
            The intermediate horizontal trackID to use if the demux wires are not connected to an adjacent layer.

        Returns
        -------
        The connected demux data wires at the given TrackID.
        """
        xm_layer = self.conn_layer + 3
        if tid.layer_id > xm_layer:
            ym_layer = xm_layer + 1
            ym_w_sig = self.tr_manager.get_width(ym_layer, 'sig')
            is_adj = False
        else:
            ym_layer, ym_w_sig = None, None
            is_adj = True
        if isinstance(warr_list, WireArray):
            warr_list = [warr_list]
        new_warrs = []
        for warr in warr_list:
            if not is_adj:
                coord_x = warr.bound_box.xm
                warr_xm = self.connect_to_tracks(warr, conn_tid, min_len_mode=MinLenMode.MIDDLE)
                tidx_vert = self.grid.coord_to_track(ym_layer, coord_x, RoundMode.NEAREST)
                tid_vert = TrackID(ym_layer, tidx_vert, width=ym_w_sig)
                warr = self.connect_to_tracks(warr_xm, tid_vert, min_len_mode=MinLenMode.MIDDLE)
            new_warrs.append(warr)
        return self.connect_to_tracks(new_warrs, tid)

    def draw_layout(self) -> None:
        pinfo = MOSBasePlaceInfo.make_place_info(self.grid, self.params['pinfo'])
        self.draw_base(pinfo)

        num_stages: int = self.params['num_stages']
        demux_params: Param = self.params['demux_params']
        use_ff_list: Union[bool, Sequence[bool]] = self.params['use_ff_list']
        din_buf_params: Optional[Param] = self.params['din_buf_params']
        is_big_endian: bool = self.params['is_big_endian']
        export_nets: bool = self.params['export_nets']
        ridx_p: int = self.params['ridx_p']
        ridx_n: int = self.params['ridx_n']
        ratio: int = self.ratio
        num_sig_hor_layers: int = self.params['num_sig_hor_layers']
        export_unit_sup: bool = self.params['export_unit_sup']
        clk_layer: int = self.params['clk_layer']
        tap_sep_unit: int = self.params['tap_sep_unit']
        if tap_sep_unit <= 0:
            # Put a tap half way. Heuristic.
            tap_sep_unit = self.ratio >> 1
        logic_tidx: int = self.params['logic_tidx']
        ptap_tidx: Optional[int] = self.params['ptap_tidx']
        ntap_tidx: Optional[int] = self.params['ntap_tidx']
        draw_taps : bool = self.params['draw_taps']

        if (ptap_tidx is None) != (ntap_tidx is None):
            raise ValueError("Either both ptap and ntap tidxes must be defined or not defined")
        
        has_substrate_rows = ptap_tidx is not None
        draw_substrate_rows = draw_taps and has_substrate_rows
        draw_tap_columns = draw_taps and not has_substrate_rows

        has_din_buf = din_buf_params is not None
        if isinstance(use_ff_list, bool):
            use_ff_list = [use_ff_list for _ in range(num_stages)]
        elif len(use_ff_list) != num_stages:
            raise ValueError(f"use_ff_list = {use_ff_list} must have length num_stages = {num_stages}")

        hm_layer = self.conn_layer + 1
        vm_layer = hm_layer + 1
        xm_layer = vm_layer + 1

        if clk_layer is None:
            clk_layer = xm_layer
        else:
            assert clk_layer >= xm_layer

        # --- Create Templates --- #
        logic_pinfo = self.get_tile_pinfo(logic_tidx)
        shared_params = dict(pinfo=logic_pinfo, ridx_p=ridx_p, ridx_n=ridx_n, vertical_sup=has_substrate_rows,
                             draw_taps=False)

        demux_params = demux_params.copy(append=dict(**shared_params, is_big_endian=is_big_endian, connect_in=False))
        demux_clk_buf_params = demux_params.get('clk_buf_params', {})
        if not demux_clk_buf_params:
            raise NotImplementedError("clk_buf_params is required for demux_params")

        # In the case that demux instances with both use_ff = True and use_ff = False are required, keep track of
        # which masters have already been created.
        demux_master_lu = {}

        if has_din_buf:
            din_buf_params = din_buf_params.copy(append=shared_params)
            din_buf_master: InvChainCore = self.new_template(InvChainCore, params=din_buf_params)
        else:
            din_buf_master = None

        # --- Place instances --- #
        num_units = ratio - 1
        demux_insts = []
        cur_col = 0

        blk_sp = self.min_sep_col
        sub_sep = self.sub_sep_col
        tap_ncols = self.get_tap_ncol(tile_idx=logic_tidx)
        vdd_conn_list, vss_conn_list = [], []

        # add left tap
        if draw_tap_columns:
            self.add_tap(cur_col, vdd_conn_list, vss_conn_list, tile_idx=logic_tidx)
            cur_col += tap_ncols + sub_sep
        
        # Data in buffer
        if has_din_buf:
            # Allocate space for the input wire on vm_layer
            sp = self.grid.get_track_pitch(vm_layer) * self.tr_manager.get_sep(vm_layer, ('sig', 'sig')) / self.sd_pitch
            cur_col += sp.up_even(True).value
            din_buf_inst = self.add_tile(din_buf_master, logic_tidx, cur_col)
            cur_col += din_buf_master.num_cols
        else:
            din_buf_inst = None

        # Floorplanning strategy: group demux unit cells by clock frequency/data rate (i.e., order from left to right
        # in a breadth-first search (BFS) style as if traversing the binary tree). This minimizes clock routing
        for unit_idx in range(num_units):
            if unit_idx > 0 and unit_idx % tap_sep_unit == 0:
                # add mid tap
                if draw_tap_columns:
                    cur_col += sub_sep
                    self.add_tap(cur_col, vdd_conn_list, vss_conn_list, tile_idx=logic_tidx)
                    cur_col += tap_ncols + sub_sep
                else:
                    cur_col += blk_sp    
            else:
                cur_col += blk_sp
            stg_idx = int(math.log2(1 + unit_idx))
            use_ff = use_ff_list[stg_idx]
            if use_ff in demux_master_lu:
                demux_master = demux_master_lu[use_ff]
            else:
                demux_master: Demux1To2 = self.new_template(Demux1To2,
                                                            params=demux_params.copy(append=dict(use_ff=use_ff)))
                demux_master_lu[use_ff] = demux_master
            demux_insts.append(self.add_tile(demux_master, logic_tidx, cur_col))
            cur_col += demux_master.num_cols
        cur_col += sub_sep

        # add right tap
        if draw_tap_columns:
            self.add_tap(cur_col, vdd_conn_list, vss_conn_list, tile_idx=logic_tidx)

        # Add substrate rows
        if draw_substrate_rows:
            tap_vss = self.add_substrate_contact(0, 0, tile_idx=ptap_tidx)
            tap_vdd = self.add_substrate_contact(0, 0, tile_idx=ntap_tidx)
            tap_vss_tid = self.get_track_id(0, MOSWireType.DS, 'sup', tile_idx=ptap_tidx)
            tap_vdd_tid = self.get_track_id(0, MOSWireType.DS, 'sup', tile_idx=ntap_tidx)
            tap_vss_hm = self.connect_to_tracks(tap_vss, tap_vss_tid)
            tap_vdd_hm = self.connect_to_tracks(tap_vdd, tap_vdd_tid)
        else:
            tap_vss_hm = None
            tap_vdd_hm = None

        self.set_mos_size()

        # --- Routing --- #
        wlookup_map = self.get_tile_pinfo(logic_tidx).wire_lookup

        # if multiple horizontal layers are used for signal routing, the xm_layer TrackID that is reserved for
        # accessing xxm_layer tracks.
        conn_tid = None
        # The maximum number of tracks that are required at the same x coordinate is set by the last stage of demuxes,
        # which have `ratio` outputs and `ratio / 2` inputs.
        num_used_tracks = ratio * 3 // 2

        if num_sig_hor_layers == 1:
            assert xm_layer in wlookup_map
            wlookup = wlookup_map[xm_layer]
            num_sig_wires = wlookup.get_num_wires('sig')
            if num_sig_wires < num_used_tracks:
                raise ValueError(f"Not enough tracks. num_sig_wires = {num_sig_wires} should be >= {num_used_tracks}.")
            sig_tids = [self.get_hm_track_id(xm_layer, 'sig', i, tile_idx=logic_tidx) for i in range(num_used_tracks)]
            clk_tid = self.get_hm_track_id(xm_layer, 'clk', tile_idx=logic_tidx)

        elif num_sig_hor_layers == 2:
            xxm_layer = xm_layer + 2
            assert xm_layer in wlookup_map
            assert xxm_layer in wlookup_map
            wlookup_xm = wlookup_map[xm_layer]
            wlookup_xxm = wlookup_map[xxm_layer]
            num_sig_xm = wlookup_xm.get_num_wires('sig')
            num_sig_xxm = wlookup_xxm.get_num_wires('sig')
            num_avail_trs = num_sig_xm + num_sig_xxm
            if num_avail_trs < num_used_tracks + 1:  # additional track required to use/via up to xxm_layer
                raise ValueError(f"Not enough tracks. Number of available tracks = {num_avail_trs} should be >= "
                                 f"{num_used_tracks + 1}.")
            if num_used_tracks <= num_sig_xm:  # only use one layer since there are sufficient tracks
                sig_tids = [self.get_hm_track_id(xm_layer, 'sig', i, tile_idx=logic_tidx) for i in range(num_used_tracks)]
            else:
                if num_sig_xm < 1:
                    raise ValueError("Must have at least 1 track on xm_layer to use xxm_layer tracks.")
                sig_tids = [self.get_hm_track_id(xm_layer, 'sig', i, tile_idx=logic_tidx) for i in range(num_sig_xm - 1)]
                conn_tid = self.get_hm_track_id(xm_layer, 'sig', num_sig_xm - 1, tile_idx=logic_tidx)
                sig_tids.extend([self.get_hm_track_id(xxm_layer, 'sig', i, tile_idx=logic_tidx) for i in
                                 range(num_used_tracks - num_sig_xm + 1)])
            clk_tid = self.get_hm_track_id(xm_layer, 'clk', tile_idx=logic_tidx)
        else:
            raise NotImplementedError(f"num_sig_hor_layers = {num_sig_hor_layers} > 2 is currently not supported.")

        demux_idx = 0
        sig_idx = 1
        # Route input
        in_warr = self.connect_sig_to_tid(demux_insts[0].get_all_port_pins('in'), sig_tids[0], conn_tid)
        if has_din_buf:
            in_buf_warr = in_warr
            self.connect_to_track_wires(din_buf_inst.get_pin('out'), in_buf_warr)
            self.add_pin('din_buf', in_buf_warr, hide=not export_nets)
            vm_tidx = self.grid.coord_to_track(vm_layer, din_buf_inst.bound_box.xl, RoundMode.NEAREST)
            vm_tid = TrackID(vm_layer, vm_tidx, width=self.tr_manager.get_width(vm_layer, 'sig'))
            vm_tid = self.tr_manager.get_next_track_obj(vm_tid, 'sig', 'sig', -1)
            in_warr = self.connect_to_tracks(din_buf_inst.get_pin('in'), vm_tid, min_len_mode=MinLenMode.MIDDLE)
            self.add_pin('din_vm', in_warr, hide=True)
            in_warr = self.connect_to_tracks(in_warr, in_buf_warr.track_id)
        else:
            self.reexport(demux_insts[0].get_port('in'), net_name='din_vm', hide=True)
        in_warr = self.extend_wires(in_warr, lower=self.bound_box.xl)
        self.add_pin('din', in_warr)

        # Route intermediate data wires and output wires
        prev_out_warrs = []
        for stg_idx in range(num_stages):
            num_blocks = 1 << stg_idx
            sub_insts = demux_insts[demux_idx:demux_idx + num_blocks]
            num_out = num_blocks * 2

            if sig_idx + num_out > num_used_tracks:  # loop around to handle overflow
                out_tids = sig_tids[sig_idx:] + sig_tids[:sig_idx + num_out - num_used_tracks]
            else:
                out_tids = sig_tids[sig_idx:sig_idx + num_out]

            out_warrs: List[Optional[WireArray]] = [None for _ in range(num_out)]
            for inst_idx, inst in enumerate(sub_insts):
                inst_out_tids = out_tids[2 * inst_idx:2 * inst_idx + 2]
                inst_out_warrs = inst.get_pin('out<0>'), inst.get_pin('out<1>')
                bit_idxes = [inst_idx, inst_idx + num_blocks]
                for bit_idx, tid, warr in zip(bit_idxes, inst_out_tids, inst_out_warrs):
                    out_warrs[bit_idx] = self.connect_sig_to_tid(warr, tid, conn_tid)

                if stg_idx > 0:
                    in_warr = self.connect_sig_to_tid(inst.get_all_port_pins('in'), prev_out_warrs[inst_idx].track_id,
                                                      conn_tid)
                    self.connect_wires([in_warr, prev_out_warrs[inst_idx]])
                    self.add_pin(f'dmid_{stg_idx - 1}<{inst_idx}>', inst.get_pin('in'), hide=not export_nets)

                if inst.has_port('clk_buf'):
                    self.reexport(inst.get_port('clk_buf'), net_name=f'clk_div_buf_{stg_idx}_{inst_idx}')
                if inst.has_port('clkb_buf'):
                    self.reexport(inst.get_port('clkb_buf'), net_name=f'clk_divb_buf_{stg_idx}_{inst_idx}')

            clk_vm_warrs = [inst.get_pin('clk') for inst in sub_insts]
            self.add_pin(f'clk_div_{stg_idx}_vm', clk_vm_warrs, hide=True)
            clk_warr = self.connect_to_tracks(clk_vm_warrs, clk_tid, min_len_mode=MinLenMode.MIDDLE)
            if clk_layer > clk_warr.layer_id:
                clk_warr = self.connect_via_stack(self.tr_manager, clk_warr, clk_layer, 'clk')
            self.add_pin(f'clk_div_{stg_idx}', clk_warr)

            demux_idx += num_blocks
            sig_idx += num_out
            prev_out_warrs = out_warrs

        for i, warr in enumerate(prev_out_warrs):
            self.add_pin(f'dout<{i}>', self.extend_wires(warr, upper=self.bound_box.xh, min_len_mode=MinLenMode.UPPER))

        if export_unit_sup:
            for inst in demux_insts:
                self.reexport(inst.get_port('VDD'))
                self.reexport(inst.get_port('VSS'))

        all_insts = list(demux_insts)
        if has_din_buf:
            all_insts.append(din_buf_inst)
        if has_substrate_rows:
            # Conn layer
            vdd_conn, vss_conn = [], []
            for inst in all_insts:
                if not inst:
                    continue
                vdd_conn += inst.get_all_port_pins('VDD', self.conn_layer)
                vss_conn += inst.get_all_port_pins('VSS', self.conn_layer)
            vss_tid = self.get_track_id(0, MOSWireType.DS, 'sup', tile_idx=ptap_tidx)
            vdd_tid = self.get_track_id(0, MOSWireType.DS, 'sup', tile_idx=ntap_tidx)
            # vdd_hm = self.connect_to_track_wires(vdd_conn, tap_vdd_hm)
            # vss_hm = self.connect_to_track_wires(vss_conn, tap_vss_hm)
            vdd_hm = self.connect_to_tracks(vdd_conn, vdd_tid)
            vss_hm = self.connect_to_tracks(vss_conn, vss_tid)
        else:
            vdd_hm = [inst.get_pin('VDD') for inst in all_insts]
            vss_hm = [inst.get_pin('VSS') for inst in all_insts]
            vdd_hm = self.connect_wires(vdd_hm, lower=self.bound_box.xl, upper=self.bound_box.xh)
            vss_hm = self.connect_wires(vss_hm, lower=self.bound_box.xl, upper=self.bound_box.xh)
        self.connect_to_track_wires(vdd_conn_list, vdd_hm)
        self.connect_to_track_wires(vss_conn_list, vss_hm)

        self.add_pin('VDD', vdd_hm)
        self.add_pin('VSS', vss_hm)

        self.sch_params = dict(
            num_stages=num_stages,
            demux_params=next(iter(demux_master_lu.values())).sch_params,
            use_ff_list=use_ff_list,
            div_chain_params=None,
            din_buf_params=din_buf_master and din_buf_master.sch_params,
            is_big_endian=is_big_endian,
            export_nets=export_nets,
        )


class DesArrayBinaryTree(MOSBase):
    """ An array of binary tree deserializers, arranged in a column. Clock divider is not included."""

    def __init__(self, temp_db: TemplateDB, params: Param, **kwargs: Any) -> None:
        MOSBase.__init__(self, temp_db, params, **kwargs)
        self._clk_div_tidx_list: List[HalfInt] = []

    @classmethod
    def get_schematic_class(cls) -> Optional[Type[Module]]:
        return bag3_digital__des_array_binary_tree

    @classmethod
    def get_params_info(cls) -> Mapping[str, str]:
        return dict(
            pinfo='The MOSBasePlaceInfo object.',
            in_width='Input word width',
            narr='Number of arrayed words',
            ndum='Number of dummy units. Must be even (half placed on each end). Defaults to 0',
            dum_unit_locs='An alternative way to specify dummy units (to allow placing dummy units in the middle). '
                          'Each provided index in the deserializer array is a dummy unit. Defaults to None',
            in_order='Input order',
            unit_params='Unit deserializer parameters',
            export_nets='True to export intermediate nets. Defaults to False',
            ridx_p='pmos row index.',
            ridx_n='nmos row index.',
            sig_locs='Signal track location dictionary.',
            export_unit_sup='True to export unit supply pins. Defaults to False.',
            clk_layer='Clock layer',
            clk_pinmode='Clock pin mode',
            logic_tidx='Tile index. Defaults to 0',
            ptap_tidx='ptap tile index. If None, assume no substrate rows are used. Defaults to None',
            ntap_tidx='ntap tile index. If None, assume no substrate rows are used. Defaults to None',
            sup_layer="Supply layer. Defaults to xxm_layer = hm_layer + 4",
        )

    @classmethod
    def get_default_param_values(cls) -> Mapping[str, Any]:
        return dict(
            in_width=1,
            narr=1,
            ndum=0,
            dum_unit_locs=None,
            in_order=None,
            export_nets=False,
            ridx_p=-1,
            ridx_n=0,
            sig_locs={},
            export_unit_sup=False,
            clk_layer=None,
            clk_pinmode=PinMode.ALL,
            logic_tidx=0,
            ptap_tidx=None,
            ntap_tidx=None,
            sup_layer=0,
        )

    @classmethod
    def process_pinfo(cls, pinfo: Union[ImmutableSortedDict, Tuple[Union[TilePattern, TilePatternElement], TileInfoTable]],
                      num_logic_tiles: int, logic_tile_name: Optional[str] = 'logic', ptap_tile_name: Optional[str] = 'ptap',
                      ntap_tile_name: Optional[str] = 'ntap', 
                      **kwargs) -> Tuple[Union[Dict, Tuple[Union[TilePattern, TilePatternElement], TileInfoTable]], List[str], bool]:
        if (ptap_tile_name is None) != (ntap_tile_name is None):
            raise ValueError("Either both ptap and ntap tidxes must be defined or not defined")

        if logic_tile_name is None:  # Assume no tiling
            assert ntap_tile_name is None
            assert ptap_tile_name is None
            return pinfo, [logic_tile_name], False

        # has_substrate_taps = ntap_tile_name is not None
        has_substrate_taps = False  # BZ 9/29 TODO: substrate taps currently does not work

        if isinstance(pinfo, ImmutableSortedDict):
            pinfo_dict = pinfo.to_dict()
            tile_order = pinfo_dict.get('tiles', None)
            if tile_order:
                tile_order = [info['name'] for info in tile_order]
            elif not has_substrate_taps:
                tile_order = [logic_tile_name] * num_logic_tiles
                pinfo_tiles = []
                for i, tile_name in enumerate(tile_order):
                    pinfo_tiles.append(dict(name=tile_name, flip=i % 2 == 0))
                pinfo_dict['tiles'] = pinfo_tiles
            else:  # construct tile order
                subblock_tile_order = [logic_tile_name] * num_logic_tiles
                tile_order = [ptap_tile_name]
                last_sub_row = tile_order[-1]
                for tile in subblock_tile_order:
                    tile_order.append(tile)
                    tile_order.append(ntap_tile_name if last_sub_row == ptap_tile_name else ptap_tile_name)
                    last_sub_row = tile_order[-1]
                pinfo_tiles = []
                for i, tile_name in enumerate(tile_order):
                    flip = tile_order[i - 1] == ntap_tile_name if tile_name == logic_tile_name else False
                    pinfo_tiles.append(dict(name=tile_name, flip=flip))
                pinfo_dict['tiles'] = pinfo_tiles

            pinfo = pinfo_dict
        else:
            assert isinstance(pinfo, tuple) and isinstance(pinfo[0], (TilePattern, TilePatternElement))
            tp = pinfo[0] if isinstance(pinfo[0], TilePattern) else pinfo[0]._info
            tile_order = [tpe.name for tpe in tp._pat_list]
        return pinfo, tile_order, has_substrate_taps

    @property
    def clk_div_tidx_list(self):
        return self._clk_div_tidx_list

    def _add_unit_insts(self, num: int, cur_tidx: int, unit_params: Mapping, unit_insts: list, 
                        unit_masters: list, unit_pinfo_list: list, has_substrate_rows: bool, 
                        tile_order: list, col_idx: int = 0):
        """Helper function to draw 'num' SenseAmp units start at tile_idx = cur_tidx.
        unit_masters, unit_insts, and unit_pinfos are used to return the respective items. 
        Returns the next available tile index (i.e. last used tile index + 1)
        """
        # if has_substrate_rows, include p + ntaps
        num_tiles = 3 if has_substrate_rows else 1
        for _ in range(num):
            unit_pinfo = self.get_draw_base_sub_pattern(cur_tidx, cur_tidx + num_tiles)
            sub_tile_order = tile_order[cur_tidx:cur_tidx + num_tiles]
            tidx_dict = dict(
                logic_tidx=sub_tile_order.index('logic')
            )
            if has_substrate_rows:
                tidx_dict.update(dict(
                    ptap_tidx=sub_tile_order.index('ptap'),
                    ntap_tidx=sub_tile_order.index('ntap')
                ))
            try:
                cache_idx = unit_pinfo_list.index(unit_pinfo)
            except ValueError:
                cur_params = unit_params.copy(append=dict(pinfo=unit_pinfo, **tidx_dict))
                unit_master: DesBinaryTree = self.new_template(DesBinaryTree, params=cur_params)
                unit_pinfo_list.append(unit_pinfo)
                unit_masters.append(unit_master)
            else:
                unit_master = unit_masters[cache_idx]
            inst = self.add_tile(unit_master, cur_tidx, col_idx)
            unit_insts.append(inst)
            cur_tidx += num_tiles
        return cur_tidx


    def draw_layout(self) -> None:
        in_width: int = self.params['in_width']
        narr: int = self.params['narr']
        ndum: int = self.params['ndum']
        dum_unit_locs: Optional[Sequence[int]] = self.params['dum_unit_locs']
        unit_params: Optional[Param] = self.params['unit_params']
        export_nets: bool = self.params['export_nets']
        ridx_p: int = self.params['ridx_p']
        ridx_n: int = self.params['ridx_n']
        export_unit_sup: bool = self.params['export_unit_sup']
        in_order: Sequence[Tuple[int, int]] = self.params['in_order']
        clk_layer: int = self.params['clk_layer']
        sig_locs: Dict[str, int] = self.params['sig_locs']
        clk_pinmode: PinMode = self.params['clk_pinmode']
        if isinstance(clk_pinmode, str):
            clk_pinmode = PinMode[clk_pinmode]
        logic_tidx: int = self.params['logic_tidx']
        ptap_tidx: Optional[int] = self.params['ptap_tidx']
        ntap_tidx: Optional[int] = self.params['ntap_tidx']
        sup_layer: int = self.params['sup_layer']
        
        # --- Create pinfo --- #

        default_in_order = [(arr_idx, bit_idx) for arr_idx in range(narr) for bit_idx in range(in_width)]
        if in_order is None:
            in_order = default_in_order
        elif sorted(in_order) != sorted(default_in_order):
            raise ValueError("in_order is an invalid permutation")

        num_units = narr * in_width

        if dum_unit_locs:
            dum_unit_locs = sorted(dum_unit_locs)
            ndum = len(dum_unit_locs)
            if max(dum_unit_locs) >= num_units + ndum or min(dum_unit_locs) < 0:
                raise ValueError(f"Invalid dum_unit_locs")
        else:
            if ndum % 2:
                raise ValueError(f"ndum = {ndum} must be even")
            ndum_half = ndum // 2
            dum_unit_locs = list(range(ndum_half)) + list(range(num_units + ndum_half, num_units + ndum))

        num_tot = num_units + ndum

        pinfo, tile_order, has_substrate_rows = self.process_pinfo(**self.params, num_logic_tiles=num_tot)
        pinfo = MOSBasePlaceInfo.make_place_info(self.grid, pinfo)
        self.draw_base(pinfo)

        # --- Create template parameters --- #

        conn_layer = self.conn_layer
        hm_layer = conn_layer + 1
        vm_layer = hm_layer + 1
        xm_layer = vm_layer + 1

        if clk_layer is None:
            clk_layer = xm_layer
        if not sup_layer:
            sup_layer = xm_layer + 2

        shared_params = dict(pinfo=pinfo, draw_taps=False)
        clk_hor_layer = clk_layer if self.grid.is_horizontal(clk_layer) else clk_layer - 1
        unit_params = unit_params.copy(append=dict(**shared_params, clk_layer=clk_hor_layer))
        dum_params = unit_params.copy(append=dict(clk_layer=None))
        
        num_stages = unit_params['num_stages']
        draw_taps = True  # TODO: is this redundant? Do we need an option without draw_taps?
        draw_substrate_rows = draw_taps and has_substrate_rows
        draw_substrate_cols = draw_taps and not has_substrate_rows

        # --- Place instances --- #

        unit_insts, unit_masters, unit_pinfo_list = [], [], []
        dum_insts, dum_masters, dum_pinfo_list = [], [], []
        tile_idx = 0
        dum_vss_vms = []

        # Determine offset for tap columns
        if draw_substrate_cols:
            sup_col_info = self.get_supply_column_info(hm_layer, 0)
            tap_ncols = sup_col_info.ncol
            col_idx = tap_ncols
        else:
            col_idx = 0

        # Add instances in each tile row
        unit_idx = 0
        for i in range(num_tot):
            if i in dum_unit_locs:
                tile_idx = self._add_unit_insts(1, tile_idx, dum_params, dum_insts, dum_masters, 
                                                dum_pinfo_list, has_substrate_rows, tile_order,
                                                col_idx)
                inst = dum_insts[-1]

                # Ties ports down
                vss_vms = inst.get_all_port_pins('din_vm')
                for stg_idx in range(num_stages):
                    vss_vms.extend(inst.get_all_port_pins(f'clk_div_{stg_idx}_vm'))
                dum_vss_vms.extend(vss_vms)
                self.connect_to_track_wires(vss_vms, inst.get_pin('VSS'))
            else:
                arr_idx, bit_idx = in_order[unit_idx]
                tile_idx = self._add_unit_insts(1, tile_idx, unit_params, unit_insts, unit_masters, 
                                                unit_pinfo_list, has_substrate_rows, tile_order,
                                                col_idx)
                inst = unit_insts[-1]

                # Reexport ports
                for port_name in inst.port_names_iter():
                    if port_name.startswith(('clk_div_', 'VDD', 'VSS')) and not port_name.startswith('clk_div_buf'):
                        continue
                    port_base, port_range = parse_cdba_name(port_name)
                    port_sfx = port_name[len(port_base):]
                    if port_base == 'din':
                        self.reexport(inst.get_port(port_name),
                                      net_name=f'{port_name}_{arr_idx}' + (f'<{bit_idx}>' if in_width > 1 else ''))
                    elif port_base == 'dout':
                        self.reexport(inst.get_port(port_name),
                                      net_name=f'{port_base}_{arr_idx}<{in_width * port_range.start + bit_idx}>')
                    else:
                        self.reexport(inst.get_port(port_name),
                                      net_name=f'{port_base}_{arr_idx}_{bit_idx}{port_sfx}')
                unit_idx += 1
        
        # Draw taps and set mos size
        if draw_substrate_rows:
            self.set_mos_size()
            for tile_idx, tile_name in enumerate(tile_order):
                if tile_name in ('ptap', 'ntap'):
                    self.add_substrate_contact(0, 0, tile_idx=tile_idx, seg=self.num_cols)
        elif draw_substrate_cols:
            unit_ncols = unit_masters[-1].num_cols
            tot_ncols = unit_ncols + 2 * tap_ncols + self.sub_sep_col
            vdd_table = {conn_layer: [], hm_layer: []}
            vss_table = {conn_layer: [], hm_layer: []}
            for i in range(num_tot):
                self.add_supply_column(sup_col_info, 0, vdd_table, vss_table, tile_idx=i)
                self.add_supply_column(sup_col_info, tot_ncols, vdd_table, 
                                       vss_table, flip_lr=True, tile_idx=i)
            self.set_mos_size(num_cols=tot_ncols)
        else:
            self.set_mos_size()

        all_insts = unit_insts + dum_insts

        # --- Routing --- #

        # Supplies
        vdd_hm = [inst.get_pin('VDD') for inst in all_insts]
        vss_hm = [inst.get_pin('VSS') for inst in all_insts]
        vdd_hm = self.connect_wires(vdd_hm, lower=self.bound_box.xl, upper=self.bound_box.xh)
        vss_hm = self.connect_wires(vss_hm, lower=self.bound_box.xl, upper=self.bound_box.xh)
        if draw_substrate_cols:
            vdd_hm = self.connect_wires(vdd_hm + vdd_table[hm_layer])
            vss_hm = self.connect_wires(vss_hm + vss_table[hm_layer])

        if sup_layer > hm_layer:
            vdd_hm_list = vdd_hm[0].to_warr_list()
            vss_hm_list = vss_hm[0].to_warr_list()
            vdd_list = [self.connect_via_stack(self.tr_manager, warr, sup_layer, 'sup') for warr in vdd_hm_list]
            vss_list = [self.connect_via_stack(self.tr_manager, warr, sup_layer, 'sup') for warr in vss_hm_list]
            self.add_pin('VDD', vdd_list, connect=True)
            self.add_pin('VSS', vss_list, connect=True)
        else:
            self.add_pin('VDD', vdd_hm, connect=True)
            self.add_pin('VSS', vss_hm, connect=True)

        if dum_vss_vms:
            self.add_pin('VSS_vm', dum_vss_vms, hide=True)

        if export_unit_sup:
            for inst in all_insts:
                self.reexport(inst.get_port('VDD'), connect=True)
                self.reexport(inst.get_port('VSS'), connect=True)

        # Clock routing
        is_hor_clk = self.grid.is_horizontal(clk_layer)
        w_clk = self.tr_manager.get_width(clk_layer, 'clk')
        clk_div_tidx_list = []
        for i in range(num_stages):
            clk_name = f'clk_div_{i}'
            clk_div_warrs = self.connect_wires([inst.get_pin(clk_name) for inst in unit_insts])
            if is_hor_clk:
                self.add_pin(clk_name, clk_div_warrs, connect=True)
            else:
                if clk_name in sig_locs:
                    clk_tidx = HalfInt.convert(sig_locs[clk_name])
                else:
                    clk_tidx = self.grid.coord_to_track(clk_layer, clk_div_warrs[0].bound_box.xm, RoundMode.NEAREST)
                clk_tid = TrackID(clk_layer, clk_tidx, width=w_clk)
                clk_div_warrs = self.connect_to_tracks(clk_div_warrs, clk_tid)
                self.add_pin(clk_name, clk_div_warrs, mode=clk_pinmode)
                clk_div_tidx_list.append(clk_tidx)
        self._clk_div_tidx_list = clk_div_tidx_list

        self.sch_params = dict(
            in_width=in_width,
            narr=narr,
            ndum=ndum,
            # unit_params=unit_master.sch_params,
            unit_params=unit_masters[0].sch_params,
            div_chain_params=None,
            export_nets=export_nets,
        )
