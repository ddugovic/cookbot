# -*- coding: utf-8 -*-

import commands
import gtk
import itertools
import math
import os
import re
import time
import wnck
import sys

from contextlib import contextmanager
from functools import wraps

from pykeyboard import PyKeyboard

from PIL import Image
from PIL import ImageOps

from cookbot.ocr import OCR
from cookbot.recipes import RECIPES, FOODS, FINISH_AT, COOKING_TIME
from cookbot.interpreter import parser

from cookbot.colorops import rgb_to_hsv

from StringIO import StringIO

if sys.platform.startswith('linux'):
    import ctypes


TIMER_BOX = [(219, 72 + i*60, 241, 72 + i*60 + 56) for i in xrange(8)]

ACTIVE_BOX = [(199, 72 + i*60, 247, 72 + i*60 + 56) for i in xrange(8)]

OUTLINES = [(248, 72 + i*60, 249, 72 + i*60 + 56) for i in xrange(8)]

CANARY_PX = (481, 36)

ROSTER = [(24, 90),
          (24, 164),
          (24, 216),
          (24, 285),
          (24, 324),
          (24, 398),
          (24, 461),
          (24, 516),
          ]


def _getcolors(im, color):
    n = im.size[0] * im.size[1]
    return {k:v for (v, k) in im.getcolors(n)}.get(color, 0)


def yellow(im):
    return _getcolors(im, (255, 242, 0))



class BaseWindow(object):
    def __init__(self):

        self._window = None
        self._img = None

        self._title = None
        self._text = None
        self._ticket_no = None
        self._orders = None

        self.k = PyKeyboard()

    def get_window(self):
        raise NotImplementedError

    def focus(self):
        raise NotImplementedError

    def grab(self, *args):
        raise NotImplementedError

    def capture(self, bbox=None):
        x, y, w, h = self.get_coords()

        img = self.grab(x, y, w, h)

        if bbox:
            img = img.crop(bbox)

        return img

    def __getattr__(self, attr):
        if attr.startswith('_'):
            raise AttributeError(attr)

        # maybe this is too much magic?
        value = getattr(self, '_' + attr)

        if value is None:
            value = getattr(self, 'get_' + attr)()
            setattr(self, '_' + attr, value)

        return value

    def refresh(self, img=None):
        t = time.time()

        self._img = img or self.capture()
        self._ocr = OCR(self._img)

        self._title = None
        self._text = None
        self._orders = None
        self._ticket_no = None
        self._active = None

    def get_title(self):
        return self._ocr.get_title()

    def get_text(self):
        return self._ocr.get_text()

    def get_orders(self):
        n = range(1, 9)
        roster = self.get_roster()
        outlines, x_factor = self.get_outlines(roster)

        return zip(n, roster, outlines, x_factor)

    def get_ticket_no(self):
        return self._ocr.get_ticket_no()

    def get_outlines(self, roster=None):
        if roster is None:
            roster = [True] * 8

        outlines = []
        x_factor = []

        for i, v in enumerate(roster):
            if not v:
                outlines.append(None)
                x_factor.append(None)
                continue

            bbox = OUTLINES[i]
            x1, y1, x2, y2 = bbox

            out = _identify_outline(self._img.crop(bbox))

            if out is not None:
                outlines.append(out)
                x_factor.append(x2)
                continue

            while x1 > 60:
                x1 -= 1
                x2 -= 1

                out = _identify_outline(self._img.crop((x1, y1, x2, y2)))

                if out is not None:
                    outlines.append(out)
                    x_factor.append(x2)
                    break

            else:
                outlines.append(None)
                x_factor.append(None)


        assert len(outlines) == 8
        assert len(x_factor) == 8

        return outlines, x_factor

    def get_roster(self):
        roster = [cdist(self._img.getpixel(b), (255, 255, 255)) < 1 for b in ROSTER]
        assert len(roster) == 8
        return roster

    @property
    def canary(self):
        return self._img.getpixel(CANARY_PX)

    def at_kitchen(self):
        return self._img.getpixel((840, 164)) == (37, 44, 139)

    def at_grill(self):
        p = self._img.getpixel((394, 283))
        return p in {(134, 134, 132), (106, 106, 104), (95, 94, 100), (85, 83, 89)}

    def key(self, k, d=0.1):
        print 'Key:', k
        self.k.press_key(k)
        time.sleep(d-0.05)
        self.k.release_key(k)
        time.sleep(d+0.05)

    def escape(self):
        self.key(self.k.escape_key)

    def change_recipe(self):
        self.key(self.k.control_key)
        time.sleep(0.1)

    def order_ok(self):
        SMILEY_BBOX = (61, 72, 221, 128)

        return max([yellow(self.capture(SMILEY_BBOX)) for x in xrange(20)]) > 800





def _identify_outline(im):
    t = im.size[0] * im.size[1]

    colors = im.getcolors(t)

    n, c = max(colors, key=lambda x: x[0])

    f = n/float(t)

    if f < 0.8:
        return None

    if cdist(c, (0, 0, 0)) < 1:
        return 'new'

    if cdist(c, (79, 79, 79)) < 1:
        return 'active'

    if cdist(c, (114, 114, 114)) < 1:
        return 'cooking'

    if cdist(c, (190, 0, 0)) < 1:
        return 'burning'

    if cdist(c, (255, 255, 64)) < 1:
        return 'ready'

    if cdist(c, (106, 255, 255)) < 1:
        return 'waiting'

    h, s, v = rgb_to_hsv(*c)

    if h == 0.5 and s == 0 and v > 100:
        return 'waiting'

    if h == 1/6.0 and s == 0 and v > 100:
        return 'ready'

    #raise RuntimeError("Cannot identify outline")




def cdist(a, b):
    # color distance
    aR, aG, aB = a
    bR, bG, bB = b

    rmean = (aR + bR) / 2

    r = aR - bR
    g = aG - bG
    b = aB - bB

    return math.sqrt((((512 + rmean)*r*r) >> 8) + 4*g*g + (((767 - rmean) * b * b) >> 8))



class GTKWindow(BaseWindow):

    _libpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_grabber.so')
    _grabber = ctypes.CDLL(_libpath)

    def grab(self, x, y, w, h):
        size = w * h
        objlength = size * 3

        self._grabber.get_screen.argtypes = []
        result = (ctypes.c_ubyte*objlength)()

        self._grabber.get_screen(x, y, w, h, result)
        return Image.frombuffer('RGB', (w, h), result, 'raw', 'RGB', 0, 1)

    def focus(self):
        self.window.activate(int(time.time()))

    def get_window(self):
        screen = wnck.screen_get_default()

        # flush gtk events
        while gtk.events_pending():
            gtk.main_iteration()

        # find the game window
        for window in screen.get_windows():
            if window.get_name() == 'CookServeDelicious':
                return window

        raise RuntimeError("Can't find game window")

    def get_coords(self):
        x, y, w, h = self.window.get_geometry()

        # remove the title bar
        h -= 28
        y += 28

        return x, y, w, h



if sys.platform.startswith('linux'):
    GameWindow = GTKWindow

else:
    raise NotImplementedError("Platform not supported: %s" % sys.platform)



if __name__ == '__main__':
    win = GameWindow()

    while 1:
        win.refresh()
        win.get_outlines()