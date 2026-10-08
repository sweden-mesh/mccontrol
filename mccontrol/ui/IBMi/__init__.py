"""IBMi: the first interface, the IBM i (AS/400, 5250) green screen, on the API.

A title line, numbered selections, a `===>` command line, lists with an Opt column where an
option number typed on a row runs when Enter is pressed, F3 to exit, F5 to refresh, F12 to
cancel, the message line above the function keys. The only module of the package it uses is
`mccontrol.apiclient`.
"""

DESCRIPTION = "IBMi: the 5250 green screen -- numbered menus, Opt columns, ===> and F3/F5/F12"


def run(client):
    """The screens over `client` (an `apiclient.Client`), until Sign off."""
    from .app import App
    from .terminal import start
    return start(lambda term: App(term, client).main())
