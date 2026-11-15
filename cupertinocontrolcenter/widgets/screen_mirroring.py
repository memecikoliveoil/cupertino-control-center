import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio, Gdk
from cupertinocontrolcenter.i18n import _

class ScreenMirroringBox(Gtk.Box):
    """Screen Mirroring tile; opens the Screen Mirroring view."""
    def __init__(self):
        super().__init__()
        
        self.set_hexpand(False)
        self.set_vexpand(False)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        
        open_button = Gtk.Button()
        open_button.set_always_show_image(True)
        open_button.connect("clicked", self._expand_wireless_menu)
        open_button.get_style_context().add_class("widgetbutton")
        open_button.set_size_request(-1, -1)
        
        # Icon follows light/dark mode (apply_dynamic_icon_theme)
        self.screenshare_icon = Gtk.Image()
        self.screenshare_icon.set_halign(Gtk.Align.CENTER)
        
        self.screenshare_icon.set_margin_start(6)   # icon offset to the right, independent of the text
        
        # One label with a line break (two labels add extra spacing)
        # Translators: tile title, split over two lines with \n. A hair space (U+200A) at
        # the end of a line can be used to optically center a short first line.
        wireless_label = Gtk.Label(label=_("Screen\nMirroring"))
        wireless_label.get_style_context().add_class("general_widget_title")
        wireless_label.set_halign(Gtk.Align.CENTER)
        wireless_label.set_justify(Gtk.Justification.CENTER)
        
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_valign(Gtk.Align.CENTER)
        grid.set_margin_start(0)
        grid.set_margin_end(0)
        grid.set_row_spacing(0)
        
        grid.attach(self.screenshare_icon, 0, 0, 1, 1)
        grid.attach(wireless_label, 0, 1, 1, 1)
        grid.show_all()
        open_button.add(grid)
        
        main_box.pack_start(open_button, False, False, 0)
        self.pack_start(main_box, False, False, 0) 
        
        self.get_style_context().add_class("styledwidgetbox")
        self.get_style_context().add_class("widgetbutton-container")
        
        # Set the icon right away so the tile doesn't change size after opening
        self.apply_dynamic_icon_theme()
        
    def apply_dynamic_icon_theme(self):
        """Picks the -dark icon when color-scheme is dark. main.py calls this on theme changes."""
        try:
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            is_dark = "dark" in settings.get_string("color-scheme").lower()
            
            if is_dark:
                self.screenshare_icon.set_from_file("icons/screenshare-dark.png")
            else:
                self.screenshare_icon.set_from_file("icons/screenshare.png")
        except Exception as e:
            self.screenshare_icon.set_from_file("icons/screenshare.png")
            print(f"Could not pick the Screen Mirroring icon: {e}")
        return False
    
    def _expand_wireless_menu(self, widget):
        """Opens the Screen Mirroring view."""
        app = Gtk.Application.get_default()
        if app and hasattr(app, 'view_stack') and app.view_stack:
            self.apply_dynamic_icon_theme()
            app.view_stack.set_visible_child_name("sm_view")
