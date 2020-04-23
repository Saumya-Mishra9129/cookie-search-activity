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

        self.nick = profile.get_nick_name()
        if profile.get_color() is not None:
            self.colors = profile.get_color().to_string().split(',')
        else:
            self.colors = ['#A0FFA0', '#FF8080']

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

        # activity sharing
        self.participants = {}
        self.joined = False

        self.connect('shared', self._shared_cb)

        if self.shared_activity:
            # we are joining the activity
            _logger.debug('We are joining an activity')

            self.connect('joined', self._joined_cb)
            self.shared_activity.connect('buddy-joined',
                                         self._buddy_joined_cb)
            self.shared_activity.connect('buddy-left', self._buddy_left_cb)
            if self.get_shared():
                self._joined_cb(self)
        else:
            # we are creating the activity
            _logger.debug("We are creating an activity")

        if 'dotlist' in self.metadata:
            self._restore()
        else:
            self._game.new_game()

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

    def _new_game_cb(self, button=None):
        ''' Start a new game. '''
        self._game.new_game()

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

    def _shared_cb(self, activity):
        _logger.debug('Game Shared')
        self._sharing_setup()

        self.shared_activity.connect('buddy-joined', self._buddy_joined_cb)
        self.shared_activity.connect('buddy-left', self._buddy_left_cb)
        self.send_new_game()
        channel = self.tubes_chan[TelepathyGLib.IFACE_CHANNEL_TYPE_TUBES]
        _logger.debug('This is my activity: offering a tube...')
        id = channel.OfferDBusTube(SERVICE, {})
        _logger.debug('Tube address: %s', channel.GetDBusTubeAddress(id))

    def _sharing_setup(self):
        _logger.debug("_sharing_setup()")

        if self.shared_activity is None:
            _logger.error('Failed to share or join activity')
            return

        self.conn = self.shared_activity.telepathy_conn
        self.tubes_chan = self.shared_activity.telepathy_tubes_chan
        self.text_chan = self.shared_activity.telepathy_text_chan
        self.tube_id = None
        self.tubes_chan[
            TelepathyGLib.IFACE_CHANNEL_TYPE_TUBES].connect_to_signal(
            'NewTube', self._new_tube_cb)

    def _list_tubes_reply_cb(self, tubes):
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        _logger.error('ListTubes() failed: %s', e)

    def _joined_cb(self, activity):
        _logger.debug("_joined_cb()")
        if not self.shared_activity:
            self._enable_collaboration()
            return

        self.joined = True
        _logger.debug('Joined an existing Game session')
        self._sharing_setup()

        _logger.debug('This is not my activity: waiting for a tube...')
        self.tubes_chan[TelepathyGLib.IFACE_CHANNEL_TYPE_TUBES].ListTubes(
            reply_handler=self._list_tubes_reply_cb,
            error_handler=self._list_tubes_error_cb)
        self._game.set_sharing(True)
        self._receive_new_game(())

    def _new_tube_cb(self, id, initiator, type, service, params, state):
        _logger.debug('New tube: ID=%d initiator=%d type=%d service=%s '
                     'params=%r state=%d', id, initiator, type, service,
                     params, state)

        if self.tube_id is not None:
            # We are already using a tube
            return

        if type != TelepathyGLib.TubeType.DBUS or \
                service != SERVICE :
            return

        channel = self.tubes_chan[TelepathyGLib.IFACE_CHANNEL_TYPE_TUBES]

        if state == TelepathyGLib.TubeState.LOCAL_PENDING:
            channel.AcceptDBusTube(id)

        # look for the initiator's D-Bus unique name
        initiator_dbus_name = None
        dbus_names = channel.GetDBusNames(id)
        for handle, name in dbus_names:
            if handle == initiator:
                _logger.debug('found initiator D-Bus name: %s', name)
                initiator_dbus_name = name
                break

        if initiator_dbus_name is None:
            _logger.error('Unable to get the D-Bus name of the tube initiator')
            return



    def _buddy_joined_cb(self, activity, buddy):
        _logger.debug('buddy joined with object path: %s', buddy.object_path())

    def _buddy_left_cb(self, activity, buddy):
        _logger.debug('buddy left with object path: %s', buddy.object_path())




    def _setup_dispatch_table(self):
        ''' Associate tokens with commands. '''
        self._processing_methods = {
            'n': [self._receive_new_game, 'get a new game grid'],
            'p': [self._receive_dot_click, 'get a dot click'],
        }



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



