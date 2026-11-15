import gi
import subprocess
import threading
import os
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Pango
from cupertinocontrolcenter.paths import STATE_DIR

if not os.path.exists(STATE_DIR):
    os.makedirs(STATE_DIR, exist_ok=True)

SELECTION_FILE = os.path.join(STATE_DIR, "airdrop_selection")   # "valent" or "localsend"
TOGGLE_FILE    = os.path.join(STATE_DIR, "airdrop_toggle_state")  # "true" / "false"

# Commands that start each sharing app. Adjust them to how the app is installed
# (e.g. "valent" for a distro package, or a Flatpak without --branch=master).
BACKEND_COMMANDS = {
    "valent": "/usr/bin/flatpak run --branch=master --arch=x86_64 --command=valent --file-forwarding ca.andyholmes.Valent",
    "localsend": "localsend_app",
}

class AirDropView(Gtk.Box):
    def __init__(self, main_connection_box):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        
        # Same layout as the Wi-Fi and Bluetooth views: spacing through margins, no
        # styledwidgetbox class (its theme rules draw the switch at a different size)
        self.set_margin_start(13); self.set_margin_end(13)   # content and separators 15 px from the window edge
        self.set_margin_top(14); self.set_margin_bottom(7)
        
        self.main_box = main_connection_box   # the Connections tile, kept in sync
        self.rows = {}

        # --- Header: title (click to go back) and switch ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        title_eb = Gtk.EventBox()
        title_eb.set_visible_window(False)
        title_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span weight='bold' size='medium'>AirDrop</span>")
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_margin_start(2)
        title_eb.add(title_lbl)
        
        title_eb.connect("button-press-event", lambda w, e: [
            self._go_back(None)
        ] if e.button == 1 else False)
        
        self.airdrop_switch = Gtk.Switch()
        self.airdrop_switch.set_halign(Gtk.Align.END)
        self.airdrop_switch.set_valign(Gtk.Align.CENTER)   # don't stretch with the row
        self.airdrop_switch.set_margin_end(1)
        
        initial_state = False
        if os.path.exists(TOGGLE_FILE):
            try:
                with open(TOGGLE_FILE, "r") as f:
                    initial_state = f.read().strip() == "true"
            except: pass
            
        self.airdrop_switch.set_active(initial_state)
        self.airdrop_switch.connect("state-set", self._on_airdrop_switch_toggled)

        header_box.pack_start(title_eb, True, True, 0)
        header_box.pack_end(self.airdrop_switch, False, False, 0)
        self.pack_start(header_box, False, False, 0)
        
        slider_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        slider_sep.set_margin_top(2); slider_sep.set_margin_bottom(0)
        slider_sep.set_margin_start(2); slider_sep.set_margin_end(2)   # 16 px from the window edge
        self.pack_start(slider_sep, False, False, 0)

        # --- App list ---
        scroll_window = Gtk.ScrolledWindow()
        scroll_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_window.set_shadow_type(Gtk.ShadowType.NONE)
        scroll_window.set_size_request(260, -1)
        scroll_window.set_propagate_natural_height(True)
        scroll_window.set_max_content_height(450)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.get_style_context().add_class("transparent-listbox")
        
        self._create_row(self.list_box, "valent", "Valent (KDE Connect)", "kdeconnect")
        self._create_row(self.list_box, "localsend", "LocalSend", "localsend")
        
        scroll_window.add(self.list_box)
        self.pack_start(scroll_window, True, True, 0)
        
        self.refresh_selection_ui()
        self.show_all()

    def _create_row(self, container, type_name, title_text, icon_fallback):
        row_shell = Gtk.ListBoxRow()
        row_shell.get_style_context().add_class("mac-wifi-row")
        row_shell.get_style_context().add_class("mac-airdrop-row")
        row_shell.set_margin_start(0); row_shell.set_margin_end(0)
        row_shell.set_margin_top(0); row_shell.set_margin_bottom(0)
        
        row_eb = Gtk.EventBox()
        row_eb.set_visible_window(False)
        
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        # Spacing as in the Wi-Fi list; the hover box is 2 px above and below the icon
        row_box.set_margin_top(2); row_box.set_margin_bottom(2)
        if self.rows:   # 2 px above every row but the first
            row_shell.set_margin_top(2)
        row_box.set_margin_start(0); row_box.set_margin_end(0)
        
        img = Gtk.Image()
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(f"icons/{type_name}.png").scale_simple(24, 24, GdkPixbuf.InterpType.BILINEAR)
            img.set_from_pixbuf(pb)
        except: 
            # Theme icon: the file the theme uses at 16 px, drawn at 24 px. Asking for 24 px
            # directly can return a different design; SVG icons stay sharp when scaled.
            pb = None
            info = Gtk.IconTheme.get_default().lookup_icon(icon_fallback, 16, 0)
            if info and info.get_filename():
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(info.get_filename(), 24, 24, True)
                except Exception:
                    pb = None
            if pb:
                img.set_from_pixbuf(pb)
            else:
                img.set_from_icon_name(icon_fallback, Gtk.IconSize.MENU)
            
        lbl_title = Gtk.Label(label=title_text, xalign=0)
        lbl_title.set_valign(Gtk.Align.CENTER)
        
        check_icon = Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.MENU)
        check_icon.set_no_show_all(True)
        check_icon.hide()
        
        row_box.pack_start(img, False, False, 0)
        row_box.pack_start(lbl_title, True, True, 0)
        row_box.pack_end(check_icon, False, False, 0)
        
        row_eb.add(row_box)
        row_eb.connect("button-press-event", lambda w, e: self._select_and_launch(e, type_name))
        
        row_shell.add(row_eb)
        container.add(row_shell)
        self.rows[type_name] = check_icon

    def _on_airdrop_switch_toggled(self, switch, state):
        """Saves the state, starts or stops the selected app, and updates the Connections tile."""
        try:
            with open(TOGGLE_FILE, "w") as f:
                f.write("true" if state else "false")
        except Exception as e:
            print(f"Could not save the AirDrop state: {e}")
            
        if self.main_box and hasattr(self.main_box, '_update_airdrop_ui'):
            if state:
                last_app = "valent"
                if hasattr(self.main_box, '_get_last_airdrop_selection'):
                    last_app = self.main_box._get_last_airdrop_selection()
                
                exec_cmd = BACKEND_COMMANDS.get(last_app, BACKEND_COMMANDS["localsend"])

                if hasattr(self.main_box, '_launch_backend_async'):
                    self.main_box._launch_backend_async(exec_cmd)
            else:
                if hasattr(self.main_box, '_kill_all_airdrop_backends'):
                    self.main_box._kill_all_airdrop_backends()

            self.main_box._update_airdrop_ui()

        # False lets the switch update its own state immediately (True would mean we set it)
        return False

    def refresh_selection_ui(self):
        current = "valent"
        if os.path.exists(SELECTION_FILE):
            try:
                with open(SELECTION_FILE, "r") as f:
                    current = f.read().strip()
            except: pass

        if os.path.exists(TOGGLE_FILE):
            try:
                with open(TOGGLE_FILE, "r") as f:
                    file_state = f.read().strip() == "true"
                self.airdrop_switch.handler_block_by_func(self._on_airdrop_switch_toggled)
                self.airdrop_switch.set_active(file_state)
                self.airdrop_switch.handler_unblock_by_func(self._on_airdrop_switch_toggled)
            except: pass
        
        for name, icon in self.rows.items():
            if name == current: 
                icon.show()
            else: 
                icon.hide()

    def _select_and_launch(self, event, app_name):
        if event.button == 1:
            os.makedirs(os.path.dirname(SELECTION_FILE), exist_ok=True)
            with open(SELECTION_FILE, "w") as f:
                f.write(app_name)
            
            self.refresh_selection_ui()
            
            if self.airdrop_switch.get_active() and self.main_box:
                if hasattr(self.main_box, '_kill_all_airdrop_backends'):
                    self.main_box._kill_all_airdrop_backends()
                
                exec_cmd = BACKEND_COMMANDS.get(app_name, BACKEND_COMMANDS["localsend"])
                if hasattr(self.main_box, '_launch_backend_async'):
                    self.main_box._launch_backend_async(exec_cmd)
            
            if self.main_box and hasattr(self.main_box, '_update_airdrop_ui'):
                self.main_box._update_airdrop_ui()
                
            return True
        return False

    def _go_back(self, widget):
        app = Gtk.Application.get_default()
        if app and hasattr(app, 'view_stack') and app.view_stack:
            app.view_stack.set_visible_child_name("main_view")
            if hasattr(app, '_force_single_resize'):
                GLib.idle_add(app._force_single_resize)
