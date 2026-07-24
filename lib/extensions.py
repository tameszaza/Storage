from flask_bcrypt import Bcrypt

try:
    from flask_sock import Sock
except ImportError:  # The rest of Tamestorage can run before optional voice deps are installed.
    Sock = None

bcrypt = Bcrypt()
sock = Sock() if Sock is not None else None
