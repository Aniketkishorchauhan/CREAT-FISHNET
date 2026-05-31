def classFactory(iface):
    from .fishnet_plugin import FishnetPlugin
    return FishnetPlugin(iface)
