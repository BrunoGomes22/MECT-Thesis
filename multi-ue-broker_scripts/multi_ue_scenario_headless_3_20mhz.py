#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: srsRAN_multi_UE
# GNU Radio version: 3.10.1.1

from gnuradio import blocks
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import zeromq




class multi_ue_scenario_headless(gr.top_block):

    def __init__(self):
        gr.top_block.__init__(self, "srsRAN_multi_UE", catch_exceptions=True)

        ##################################################
        # Variables
        ##################################################
        self.zmq_timeout = zmq_timeout = 100
        self.zmq_hwm = zmq_hwm = -1
        self.ue3_path_loss_db = ue3_path_loss_db = 0
        self.ue2_path_loss_db = ue2_path_loss_db = 0
        self.ue1_path_loss_db = ue1_path_loss_db = 0
        self.slow_down_ratio = slow_down_ratio = 1
        self.samp_rate = samp_rate = 23040000

        ##################################################
        # Blocks
        ##################################################
        self.zeromq_req_source_1_0 = zeromq.req_source(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2201', zmq_timeout, False, zmq_hwm)
        self.zeromq_req_source_1 = zeromq.req_source(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2101', zmq_timeout, False, zmq_hwm)
        self.zeromq_req_source_0_0 = zeromq.req_source(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2301', zmq_timeout, False, zmq_hwm)
        self.zeromq_req_source_0 = zeromq.req_source(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2000', zmq_timeout, False, zmq_hwm)
        self.zeromq_rep_sink_0_2 = zeromq.rep_sink(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2300', 100, False, zmq_hwm)
        self.zeromq_rep_sink_0_1 = zeromq.rep_sink(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2001', zmq_timeout, False, zmq_hwm)
        self.zeromq_rep_sink_0_0 = zeromq.rep_sink(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2200', zmq_timeout, False, zmq_hwm)
        self.zeromq_rep_sink_0 = zeromq.rep_sink(gr.sizeof_gr_complex, 1, 'tcp://127.0.0.1:2100', zmq_timeout, False, zmq_hwm)
        self.blocks_throttle_0 = blocks.throttle(gr.sizeof_gr_complex*1, 1.0*samp_rate/(1.0*slow_down_ratio),True)
        self.blocks_multiply_const_vxx_0_1_1 = blocks.multiply_const_cc(10**(-1.0*ue2_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_1_0 = blocks.multiply_const_cc(10**(-1.0*ue3_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_1 = blocks.multiply_const_cc(10**(-1.0*ue1_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_0_0 = blocks.multiply_const_cc(10**(-1.0*ue3_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_0 = blocks.multiply_const_cc(10**(-1.0*ue2_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(10**(-1.0*ue1_path_loss_db/20.0))
        self.blocks_add_xx_0 = blocks.add_vcc(1)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_add_xx_0, 0), (self.zeromq_rep_sink_0_1, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_add_xx_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.blocks_multiply_const_vxx_0_0_0, 0), (self.blocks_add_xx_0, 2))
        self.connect((self.blocks_multiply_const_vxx_0_1, 0), (self.zeromq_rep_sink_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0_1_0, 0), (self.zeromq_rep_sink_0_2, 0))
        self.connect((self.blocks_multiply_const_vxx_0_1_1, 0), (self.zeromq_rep_sink_0_0, 0))
        self.connect((self.blocks_throttle_0, 0), (self.blocks_multiply_const_vxx_0_1, 0))
        self.connect((self.blocks_throttle_0, 0), (self.blocks_multiply_const_vxx_0_1_0, 0))
        self.connect((self.blocks_throttle_0, 0), (self.blocks_multiply_const_vxx_0_1_1, 0))
        self.connect((self.zeromq_req_source_0, 0), (self.blocks_throttle_0, 0))
        self.connect((self.zeromq_req_source_0_0, 0), (self.blocks_multiply_const_vxx_0_0_0, 0))
        self.connect((self.zeromq_req_source_1, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.zeromq_req_source_1_0, 0), (self.blocks_multiply_const_vxx_0_0, 0))


    def get_zmq_timeout(self):
        return self.zmq_timeout

    def set_zmq_timeout(self, zmq_timeout):
        self.zmq_timeout = zmq_timeout

    def get_zmq_hwm(self):
        return self.zmq_hwm

    def set_zmq_hwm(self, zmq_hwm):
        self.zmq_hwm = zmq_hwm

    def get_ue3_path_loss_db(self):
        return self.ue3_path_loss_db

    def set_ue3_path_loss_db(self, ue3_path_loss_db):
        self.ue3_path_loss_db = ue3_path_loss_db
        self.blocks_multiply_const_vxx_0_0_0.set_k(10**(-1.0*self.ue3_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_1_0.set_k(10**(-1.0*self.ue3_path_loss_db/20.0))

    def get_ue2_path_loss_db(self):
        return self.ue2_path_loss_db

    def set_ue2_path_loss_db(self, ue2_path_loss_db):
        self.ue2_path_loss_db = ue2_path_loss_db
        self.blocks_multiply_const_vxx_0_0.set_k(10**(-1.0*self.ue2_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_1_1.set_k(10**(-1.0*self.ue2_path_loss_db/20.0))

    def get_ue1_path_loss_db(self):
        return self.ue1_path_loss_db

    def set_ue1_path_loss_db(self, ue1_path_loss_db):
        self.ue1_path_loss_db = ue1_path_loss_db
        self.blocks_multiply_const_vxx_0.set_k(10**(-1.0*self.ue1_path_loss_db/20.0))
        self.blocks_multiply_const_vxx_0_1.set_k(10**(-1.0*self.ue1_path_loss_db/20.0))

    def get_slow_down_ratio(self):
        return self.slow_down_ratio

    def set_slow_down_ratio(self, slow_down_ratio):
        self.slow_down_ratio = slow_down_ratio
        self.blocks_throttle_0.set_sample_rate(1.0*self.samp_rate/(1.0*self.slow_down_ratio))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.blocks_throttle_0.set_sample_rate(1.0*self.samp_rate/(1.0*self.slow_down_ratio))




def main(top_block_cls=multi_ue_scenario_headless, options=None):
    tb = top_block_cls()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    # Attach with unequal path loss. srsUE (NR) always sends preamble 0, and the
    # 3 UEs see the cell from the same instant, so they send it in the same
    # PRACH occasions and their Msg3 overlap. With equal power every Msg3 fails
    # (crc=KO) until preambleTransMax. With 0/10/20 dB of path loss the
    # strongest Msg3 still decodes (~10 dB SINR): ue1 attaches first, then ue2,
    # then ue3, which also gives them the F1AP IDs 0/1/2.
    # Muting UEs instead does not work: a muted UE stays in cell search and,
    # since the broker runs in lockstep with every UE, slows the whole radio to
    # ~0.1x real time, so the CU's 2 s RRC timers expire.
    # After UE_EQUALIZE_AFTER_S all path losses go back to 0 dB (equal
    # channels for the experiments). UE_ATTACH_PATH_LOSS_DB="0,0,0" disables it.
    import os
    import threading
    attach_loss = [float(x) for x in os.environ.get('UE_ATTACH_PATH_LOSS_DB', '0,10,20').split(',')]
    equalize_after_s = float(os.environ.get('UE_EQUALIZE_AFTER_S', '45'))
    setters = [tb.set_ue1_path_loss_db, tb.set_ue2_path_loss_db, tb.set_ue3_path_loss_db]
    for setter, loss in zip(setters, attach_loss):
        setter(loss)
    print(f'attach path loss (dB) ue1..ue3: {attach_loss}', flush=True)

    tb.start()

    def equalize():
        for setter in setters:
            setter(0)
        print('path loss back to 0 dB for all UEs', flush=True)

    if any(attach_loss):
        timer = threading.Timer(equalize_after_s, equalize)
        timer.daemon = True
        timer.start()
        print(f'equalizing in {equalize_after_s:.0f} s', flush=True)

    # In a terminal Enter quits. Without one (systemd unit, dashboard, nohup)
    # stdin is empty: run until SIGINT/SIGTERM (sig_handler) instead of quitting.
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            input('Press Enter to quit: ')
        except EOFError:
            pass
    else:
        print('running until SIGINT/SIGTERM', flush=True)
        while True:
            signal.pause()
    tb.stop()
    tb.wait()


if __name__ == '__main__':
    main()
