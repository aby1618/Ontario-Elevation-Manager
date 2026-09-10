def classFactory(iface):
    from .plugin import OntarioDTMManagerPlugin
    return OntarioDTMManagerPlugin(iface)
