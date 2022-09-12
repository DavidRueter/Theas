def test_google(th_handler, *args, **kwarges):
    th_handler.redirect('http://www.google.com')

def test_ibm(th_handler, *args, **kwargs):
    th_handler.redirect('http://www.ibm.com')

