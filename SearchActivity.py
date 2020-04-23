# Copyright (c) 2011 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('TelepathyGLib', '0.12')

from gi.repository import Gtk, Gdk

from sugar3.activity import activity
from sugar3 import profile
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton

from toolbar_utils import button_factory, label_factory, separator_factory
from utils import json_load, json_dump, convert_seconds_to_minutes

from gi.repository import TelepathyGLib
import dbus
from dbus.service import signal
from dbus.gi_service import ExportedGObject
from sugar3.presence import presenceservice
from sugar3.presence.tubeconn import TubeConnection

from gettext import gettext as _

import json
from json import load as jload
from json import dump as jdump
from io import StringIO

try:
    from sugar3.presence.wrapper import CollabWrapper
except (ImportError, ModuleNotFoundError):
    from textchannelwrapper import CollabWrapper

from game import Game
PATH = '/org/sugarlabs/CookieSearchActivity'

import logging
_logger = logging.getLogger('cookie-search-activity')


SERVICE = 'org.sugarlabs.CookieSearchActivity'
IFACE = SERVICE


class SearchActivity(activity.Activity):
    """ Searching strategy game """

    def __init__(self, handle):
        """ Initialize the toolbars and the game board """
        try:
            super(SearchActivity, self).__init__(handle)
        except dbus.exceptions.DBusException as e:
            _logger.error(str(e))

        self.path = activity.get_bundle_path()
        self.all_scores = []
        self._restoring = True
        self.nick = profile.get_nick_name()
        if profile.get_color() is not None:
            self.colors = profile.get_color().to_string().split(',')
        else:
            self.colors = ['#A0FFA0', '#FF8080']
        self.buddy = None
        self.opponent_colors = None

        self._setup_toolbars()
        self._setup_dispatch_table()

        # Create a canvas
        canvas = Gtk.DrawingArea()
        canvas.set_size_request(Gdk.Screen.width(),
                                Gdk.Screen.height())
        self.set_canvas(canvas)
        canvas.show()
        self.show_all()

        self._game = Game(canvas, parent=self, path=self.path,
                          colors=self.colors)

        self.connect('shared', self._shared_cb)
        self.connect('joined', self._joined_cb)
        self._restoring = False

        self.collab = CollabWrapper(self)
        self.collab.connect('message', self._message_cb)
        self.collab.connect('joined', self._joined_cb)
        self.collab.setup()

        # Send the nick to our opponent
        if not self.collab.props.leader:
            self.send_nick()
            # And let the sharer know we've joined
            self.send_join()

        if 'dotlist' in self.metadata:
            self._restore()
        else:
            self._game.new_game()

    def set_data(self, data):
        pass

    def get_data(self):
        return None

    def _setup_toolbars(self):
        """ Setup the toolbars. """

        self.max_participants = 4

        toolbox = ToolbarBox()

        # Activity toolbar
        activity_button = ActivityToolbarButton(self)

        toolbox.toolbar.insert(activity_button, 0)
        activity_button.show()

        self.set_toolbar_box(toolbox)
        toolbox.show()
        self.toolbar = toolbox.toolbar

        export_scores = button_factory(
            'score-copy',
            activity_button,
            self._write_scores_to_clipboard,
            tooltip=_('Export scores to clipboard'))

        self._new_game_button_h = button_factory(
            'new-game',
            self.toolbar,
            self._new_game_cb,
            tooltip=_('Start a new game.'))

        self.status = label_factory(self.toolbar, '', width=300)

        separator_factory(toolbox.toolbar, True, False)

        stop_button = StopButton(self)
        toolbox.toolbar.insert(stop_button, -1)
        stop_button.show()


    # Collaboration-related methods

    def _shared_cb(self, activity):
        ''' Either set up initial share...'''
        _logger.debug('shared')
        self.after_share_join(True)

    def _joined_cb(self, activity):
        ''' ...or join an exisiting share. '''
        _logger.debug('joined')
        self.after_share_join(False)
        self.send_nick()
        # And let the sharer know we've joined
        self.send_join()

    def after_share_join(self, sharer):
        self._game.set_sharing(True)
        self._restoring = True

    def _message_cb(self, collab, buddy, msg):
        ''' Data from a tube has arrived. '''
        command = msg.get("command")
        payload = msg.get("payload")
        self._processing_methods[command][0](payload)

    def send_join(self):
        _logger.debug('send_join')
        self.send_event("j", self.nick)

    def send_nick(self):
        _logger.debug('send_nick')
        self.send_event("N", self.nick)
        self.send_event("C", "%s,%s" % (self.colors[0], self.colors[1]))


    def write_file(self, file_path):
        """ Write the grid status to the Journal """
        dot_list = self._game.save_game()
        self.metadata['dotlist'] = ''
        for dot in dot_list:
            self.metadata['dotlist'] += str(dot)
            if dot_list.index(dot) < len(dot_list) - 1:
                self.metadata['dotlist'] += ' '
        self.metadata['all_scores'] = \
            self._data_dumper(self.all_scores)
        self.metadata['current_gametime'] = self._game._game_time_seconds
        self.metadata['current_level'] = self._game.level

    def _data_dumper(self, data):
        io = StringIO()
        jdump(data, io)
        return io.getvalue()

    def _restore(self):
        """ Restore the game state from metadata """
        if 'current_gametime' in self.metadata:
            # '-1' Workaround for showing last second
            self._game._game_time_seconds = self._data_loader(
                self.metadata['current_gametime']) - 1
        else:
            self._game._game_time_seconds = 0;
        self._game._game_time = convert_seconds_to_minutes(
            self._game._game_time_seconds)

        if 'current_level' in self.metadata:
            self._game.level = self._data_loader(self.metadata['current_level'])

        if 'dotlist' in self.metadata:
            dot_list = []
            dots = self.metadata['dotlist'].split()
            for dot in dots:
                dot_list.append(int(dot))
            self._game.restore_game(dot_list)

        if 'all_scores' in self.metadata:
            self.all_scores = self._data_loader(self.metadata['all_scores'])
        else:
            self.all_scores = []
        _logger.debug(self.all_scores)

    def _data_loader(self, data):
        io = StringIO(data)
        return jload(io)

    def _write_scores_to_clipboard(self, button=None):
        ''' SimpleGraph will plot the cululative results '''
        _logger.debug(self.all_scores)
        scores = ''
        for i, s in enumerate(self.all_scores):
            scores += '%s: %s\n' % (str(i + 1), s)
        Gtk.Clipboard().set_text(scores)


    def _setup_dispatch_table(self):
        ''' Associate tokens with commands. '''
        self._processing_methods = {
            'n': [self._receive_new_game, 'get a new game grid'],
            'p': [self._receive_dot_click, 'get a dot click'],
        }

    def send_event(self, payload):
        ''' Send event through the tube. '''
        if hasattr(self, 'collab') and self.collab is not None:
            self.collab.post(dict(
                payload=payload
            ))

    def send_new_game(self):
        ''' Send a new grid to all players '''
        self.send_event('n|%s' % (json_dump(self._game.save_game())))

    def _receive_new_game(self, payload):
        ''' Sharer can start a new game. '''
        dot_list = json_load(payload)
        self._game.restore_game(dot_list)

    def send_dot_click(self, dot, color):
        ''' Send a dot click to all the players '''
        self.send_event('p|%s' % (json_dump([dot, color])))

    def _receive_dot_click(self, payload):
        ''' When a dot is clicked, everyone should change its color. '''
        (dot, color) = json_load(payload)
        self._game.remote_button_press(dot, color)



