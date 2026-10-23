import os
from pathlib import Path
import re
import shlex
import shutil

SEPARATORS = {';', '&&', '||', '|', '&', '\n'}


def shell_headers(command):
    pending = []
    headers = []
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, strip_tabs = pending[0]
            candidate = line.rstrip('\r\n')
            if strip_tabs:
                candidate = candidate.lstrip('\t')
            if candidate == delimiter:
                pending.pop(0)
            continue
        headers.append(line)
        if '<<' not in line:
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars='<')
        lexer.whitespace_split = True
        lexer.commenters = '#'
        tokens = list(lexer)
        for i, token in enumerate(tokens[:-1]):
            if token != '<<':
                continue
            delimiter = tokens[i + 1]
            strip_tabs = delimiter.startswith('-')
            if delimiter == '-' and i + 2 < len(tokens):
                delimiter = tokens[i + 2]
            elif strip_tabs:
                delimiter = delimiter[1:]
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', delimiter):
                raise ValueError('Use a standard identifier delimiter for reviewed heredoc commands.')
            pending.append((delimiter, strip_tabs))
    if pending:
        raise ValueError('Unterminated heredoc; separate shell diagnostics from reviewed operations.')
    return ''.join(headers)


def segments(command):
    lexer = shlex.shlex(shell_headers(command), posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = '#'
    current = []
    for token in lexer:
        if token in SEPARATORS:
            if current:
                yield current
                current = []
        else:
            current.append(token)
    if current:
        yield current


def assignment_overrides(assignment):
    if assignment.startswith('GIT_CONFIG') or assignment.startswith('GIT_INDEX_FILE='):
        raise ValueError('Do not override GIT_CONFIG* or GIT_INDEX_FILE in agent commands. Use the active Git hooks.')
    return assignment.startswith(('HOME=', 'XDG_CONFIG_HOME=')), assignment.startswith('PATH=')


def env_options(args):
    home_override = False
    path_override = False
    while args and args[0].startswith('-'):
        option = args.pop(0)
        if option == '--':
            break
        if option == '-':
            home_override = True
            continue
        if option.startswith('--'):
            name, separator, value = option.partition('=')
            if name == '--split-string':
                raise ValueError('Pass separate arguments to env for Git bypass inspection.')
            if name in {'--unset', '--chdir', '--argv0'}:
                if not separator:
                    if not args:
                        raise ValueError(f'The env option {name} requires an argument.')
                    value = args.pop(0)
                home_override = home_override or (name == '--unset' and value in {'HOME', 'XDG_CONFIG_HOME'})
            elif name == '--ignore-environment' and not separator:
                home_override = True
            elif name in {'--block-signal', '--default-signal', '--ignore-signal'}:
                continue
            elif name in {'--null', '--debug', '--list-signal-handling', '--help', '--version'} and not separator:
                continue
            else:
                raise ValueError(f'Unsupported env option {option!r}. Pass Git arguments directly.')
            continue
        for index, flag in enumerate(option[1:], 2):
            if flag == 'i':
                home_override = True
            elif flag == 'S':
                raise ValueError('Pass separate arguments to env for Git bypass inspection.')
            elif flag in {'u', 'C', 'P', 'a'}:
                value = option[index:]
                if not value:
                    if not args:
                        raise ValueError(f'The env option -{flag} requires an argument.')
                    value = args.pop(0)
                home_override = home_override or (flag == 'u' and value in {'HOME', 'XDG_CONFIG_HOME'})
                path_override = path_override or flag == 'P'
                break
            elif flag not in {'0', 'v'}:
                raise ValueError(f'Unsupported env option -{flag}. Pass Git arguments directly.')
    if args and args[0] == '-':
        args.pop(0)
        home_override = True
    while args and '=' in args[0]:
        changed_home, changed_path = assignment_overrides(args.pop(0))
        home_override = home_override or changed_home
        path_override = path_override or changed_path
    return home_override, path_override


def executables(args, cwd):
    args = list(args)
    path_override = False
    home_override = False
    while args:
        name = Path(args[0]).name
        if re.match(r'[A-Za-z_][A-Za-z_0-9]*=', args[0]):
            changed_home, changed_path = assignment_overrides(args.pop(0))
            home_override = home_override or changed_home
            path_override = path_override or changed_path
        elif name in {'env', 'genv'}:
            args.pop(0)
            cleared_home, changed_path = env_options(args)
            home_override = home_override or cleared_home
            path_override = path_override or changed_path
        elif name in {'command', 'exec'}:
            args.pop(0)
            if args and args[0] == '--':
                args.pop(0)
        elif name == 'rtk':
            if home_override:
                raise ValueError('Do not change HOME or XDG_CONFIG_HOME before Git wrappers. Use the active Git hooks.')
            args, shell = rtk_command(args[1:], cwd, path_override)
            if shell is not None:
                if path_override:
                    raise ValueError('Remove the PATH override or pass separate arguments to the RTK wrapper.')
                return [], shell
        else:
            if home_override and name in {'git', 'gmc', 'sh', 'bash', 'zsh'}:
                raise ValueError('Do not change HOME or XDG_CONFIG_HOME before Git commands. Use the active Git hooks.')
            return args, None
    return [], None


def rtk_proxy_words(command):
    words = []
    word = None
    quote = None
    chars = iter(command)
    for character in chars:
        if character in ' \t\n' and quote is None:
            if word is not None:
                words.append(word)
                word = None
            continue
        if word is None:
            word = ''
        if character == '\\' and quote != "'":
            escaped = next(chars, None)
            if escaped is None:
                break
            if quote == '"' and escaped not in '$`"\\\n':
                word += '\\'
            word += escaped
        elif character in "'\"" and quote is None:
            quote = character
        elif character == quote:
            quote = None
        else:
            word += character
    if quote is not None:
        raise ValueError('Close the quotes in the RTK proxy command string.')
    if word is not None:
        words.append(word)
    return words


def rtk_command(args, cwd, path_override=False):
    args = list(args)
    while args and (args[0] in {'--verbose', '--ultra-compact', '--skip-env'} or re.fullmatch(r'-v+', args[0])):
        args.pop(0)
    if not args or args[0] in {'-h', '--help', '-V', '--version'}:
        return [], None
    wrapper = args[0]
    if wrapper not in {'run', 'proxy', 'err', 'summary', 'test'}:
        return args, None
    args.pop(0)
    shell = None
    command = None
    while args:
        option = args[0]
        if option == '--':
            args.pop(0)
            break
        if option in {'--ultra-compact', '--skip-env'}:
            args.pop(0)
        elif option in {'-h', '--help'}:
            return [], None
        elif option in {'--shell', '-c', '--command'}:
            if len(args) < 2:
                raise ValueError('Incomplete RTK execution option.')
            value = args[1]
            del args[:2]
            if option == '--shell':
                shell = value
            else:
                command = value
        elif option.startswith('--shell='):
            shell = args.pop(0).split('=', 1)[1]
        elif option.startswith('--command=') or option.startswith('-c'):
            command = args.pop(0)
            command = command.split('=', 1)[1] if command.startswith('--') else command[2:]
        else:
            break
    if command is not None:
        return [], command
    if shell is not None and wrapper in {'err', 'summary', 'test'}:
        if len(args) != 1:
            raise ValueError('RTK shell execution requires one command string.')
        return [], args[0]
    if len(args) == 1:
        program = args[0]
        if wrapper == 'proxy':
            parts = rtk_proxy_words(program)
            if len(parts) > 1:
                return parts, None
        elif not program or any(c in program for c in ' \t\n\r\'"\\*?[]{}()$&;|<>`!#~=^'):
            if path_override:
                raise ValueError('Remove the PATH override or pass separate arguments to the RTK wrapper.')
            search_path = os.pathsep.join(str(cwd / part) for part in os.get_exec_path())
            resolved = str(cwd / program) if '/' in program else program
            if shutil.which(resolved, path=search_path) is None:
                if '/' not in program:
                    return [], program
                try:
                    (cwd / program).stat()
                except FileNotFoundError:
                    return [], program
    return args, None


def protected_config(value):
    key = value.split('=', 1)[0].lower()
    return key == 'core.hookspath' or key == 'include.path' or key.startswith(('hook.', 'includeif.', 'alias.'))


def verify_config(options):
    reads = {'get', 'list', '--get', '--get-all', '--get-regexp', '--get-urlmatch', '--get-color', '--get-colorbool', '--list', '-l'}
    writes = {'set', 'unset', 'rename-section', 'remove-section', 'edit', '--add', '--replace-all', '--unset', '--unset-all', '--rename-section', '--remove-section', '--edit', '-e'}
    values = {'-f', '--file', '--blob', '--type', '--default', '--comment', '--value', '--url'}
    flags = {'--global', '--system', '--local', '--worktree', '--null', '-z', '--name-only', '--show-origin', '--show-scope', '--includes', '--no-includes', '--fixed-value', '--all', '--regexp', '--append', '--show-names', '--no-name', '--bool', '--int', '--bool-or-int', '--path', '--expiry-date'}
    actions = []
    positional = []
    unknown = False
    i = 0
    if options and options[0] in {'get', 'list', 'set', 'unset', 'rename-section', 'remove-section', 'edit'}:
        actions.append(options[0])
        i = 1
    while i < len(options):
        arg = options[i]
        if arg == '--':
            positional = options[i + 1:]
            break
        if not arg.startswith('-'):
            positional = options[i:]
            break
        option = arg.split('=', 1)[0]
        if option in values:
            i += 1 if '=' in arg else 2
        elif arg.startswith('-f') and not arg.startswith('--'):
            i += 1
        else:
            if arg in reads or arg in writes:
                actions.append(arg)
            elif arg not in flags:
                unknown = True
            i += 1
    readonly = not unknown and not any(action in writes for action in actions) and (bool(actions) or len(positional) == 1)
    sections = any(action in {'rename-section', '--rename-section', 'remove-section', '--remove-section'} for action in actions)
    targets = positional[:2] if sections else positional[:1]
    if unknown:
        targets = options
    if not readonly and any(protected_config(target) or (sections and target.lower() in {'core', 'include', 'includeif', 'hook', 'alias'}) for target in targets):
        raise ValueError('Do not change Git hook configuration in agent commands.')


def git_action(args):
    i = 1
    while i < len(args):
        arg = args[i]
        if arg in {'-c', '--config-env', '-C', '--git-dir', '--work-tree', '--namespace'}:
            if i + 1 >= len(args):
                raise ValueError('Incomplete Git global option.')
            if arg == '--config-env' or (arg == '-c' and protected_config(args[i + 1])):
                raise ValueError('Do not override Git hook configuration in agent commands.')
            i += 2
        elif arg.startswith('-c') and not arg.startswith('--'):
            if protected_config(arg[2:]):
                raise ValueError('Do not override Git hook configuration in agent commands.')
            i += 1
        elif arg.startswith('--config-env='):
            raise ValueError('Do not override Git configuration through environment variables.')
        elif arg.startswith('-'):
            i += 1
        else:
            return arg, args[i + 1:]
    return '', []


def verify_options(action, options):
    values = {'-m', '--message', '-F', '--file', '-C', '--reuse-message', '-c', '--reedit-message', '--author', '--date', '--cleanup', '--trailer', '-S'}
    if action == 'push':
        values = {'-o', '--push-option', '--receive-pack', '--exec', '--repo'}
    elif action == 'am':
        values = {'-C', '-p', '--directory', '--exclude', '--include', '--patch-format', '--resolvemsg', '--whitespace', '--quoted-cr'}
    i = 0
    while i < len(options):
        arg = options[i]
        if arg == '--':
            break
        bypass = arg.startswith('--no-v')
        if action in {'commit', 'am'} and arg.startswith('-') and not arg.startswith('--'):
            for option in arg[1:]:
                if option in ('CpS' if action == 'am' else 'mFCcS'):
                    break
                bypass = bypass or option == 'n'
        if bypass:
            raise ValueError('Do not use --no-verify or commit -n. Resolve the Git hook failure instead.')
        i += 2 if arg in values else 1


def guard(command, cwd):
    for raw in segments(command):
        args, shell = executables(raw, cwd)
        if shell is not None:
            guard(shell, cwd)
            continue
        if not args:
            continue
        name = Path(args[0]).name
        if name in {'export', 'declare', 'typeset', 'readonly'}:
            if any(re.match(r'GIT_(CONFIG[^=]*|INDEX_FILE)=', arg) for arg in args[1:]):
                raise ValueError('Do not export GIT_CONFIG* or GIT_INDEX_FILE overrides in agent commands.')
        elif name == 'cd' and len(args) == 2:
            cwd = (cwd / args[1]).resolve()
        elif name in {'sh', 'bash', 'zsh'} and '-c' in args:
            index = args.index('-c') + 1
            if index < len(args):
                guard(args[index], cwd)
        elif name == 'git':
            action, options = git_action(args)
            if action in {'commit', 'push', 'am'}:
                verify_options(action, options)
            elif action == 'send-pack':
                raise ValueError('Do not use git send-pack. Use git push through the active Git hooks.')
            elif action == 'config':
                verify_config(options)
        elif name == 'gmc':
            verify_options('commit', args[1:])
