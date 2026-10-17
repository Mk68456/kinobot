"""Execute the bot as its service user using POSIX facilities already in Python."""

import os
import sys


def main(argv=None):
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        raise SystemExit('Missing command to execute')
    environment = os.environ.copy()
    if os.getuid() == 0:
        import pwd

        account = pwd.getpwnam('bot')
        # Drop supplementary groups before giving up root privileges.
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        environment['HOME'] = account.pw_dir
        environment['USER'] = account.pw_name
        environment['LOGNAME'] = account.pw_name
    # Replace this process so signals and exit status reach the bot directly.
    os.execvpe(command[0], command, environment)


if __name__ == '__main__':
    main()
