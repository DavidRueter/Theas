# SysWebResources may specify an on_before or an on_after function.
# Those functions are assumed to be located in this file.
# See TheasServer.py getattr(TheasCustom, this_resource.on_before) (about line 2863)
# and getattr(TheasCustom, this_resource.on_after) (about line 2883)

def test_google(th_handler, *args, **kwargs):
    th_handler.redirect('http://www.google.com')

def test_ibm(th_handler, *args, **kwargs):
    th_handler.redirect('http://www.ibm.com')

