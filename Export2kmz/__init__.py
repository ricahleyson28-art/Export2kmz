def classFactory(iface):
    from .export2kmz import Export2KmzPlugin
    return Export2KmzPlugin(iface)
