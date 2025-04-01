import sys
import time
import subprocess
import concurrent.futures
import logging


PWD_GEN = '123456789123456789'


def help():
    print('\n'.join([
        'python3 ald.py <mode> <name> <start> <stop> <chunk> <threads> <*domain> <*pwd>',
        '\tнапр.:\tpython3 ald.py usermk user 0 1000 100 5',
        '\t\tpython3 ald.py hostmk host 0 65000 5000 5 domain.ald.pro',
        '\t\tpython3 ald.py groupmk group 0 200 10 5',
        '\t\tpython3 ald.py useract user 0 1000 100 5 P@ssw0rd',
        '\t\tpython3 ald.py userauth user 0 1000 100 5 P@ssw0rd',
        '',
        'mode\tstring\tРежим работы:\tusermk - Создание учетных записей пользователей',
        '\t\t\t\thostmk - Создание учетных записей компьютеров',
        '\t\t\t\tgroupmk - Создание групп пользователей',
        '\t\t\t\tuseract - Активация учетных записей пользователей',
        '\t\t\t\tuserauth - Массовая аутентикация пользователей с активными УЗ по протоколу Kerberos',
        '\t\tЗапуск в режиме "userauth" возможен на контроллере домена или клиенте.',
        '\t\tЗапуск в остальных режимах возможен только на контроллере домена.',
        '',
        'name\tstring\tБазовое имя сущности; будут созданы или использованы записи вида "<name>0", "<name>1", ..., "<name>100500"',
        '',
        'start\t0 < int\tИндекс первой записи',
        'stop\t0 < int\tИндекс последней записи',
        '',
        'chunk\t0 < int\tРазмер блока создаваемых записей',
        'threads\t0 < int\tМаксимальное количество потоков, влияет на нагрузку и скорость исполнения',
        '\t\tЕсли установлено на 1 - команды будут выполняться последовательно.',
        '',
        'domain\tstring\tДомен - необходимо для режима "hostmk"',
        'pwd\tstring\tПароль - необходимо для режимов "useract" и "userauth"',
    ]))
    quit()


def run_cmd(cmd, name):
    cmd_str = ' '.join(cmd)
    logging.info(f'running: {cmd_str}')
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
    )
    err = process.stderr.read().decode().strip()
    if err:
        logging.error(f'{name}: {err}')
        return f'failed: {name}'
    out = process.stdout.readlines()
    if len(out) > 0:
        return f'{name}: {out[1].decode().strip()}'


def make_users(*args):
    name = str(next(*args[0]))
    cmd = [
        'ipa',
        'user-add', name,
        '--last', name,
        '--first', name
    ]
    return run_cmd(cmd, name)


def make_hosts(args):
    name = next(args[0])
    dnszone = args[1]
    ip = next(args[2])
    cmd = [
        'ipa', 'host-add', '.'.join([name, dnszone]), '--force'
    ]
    run_cmd(cmd, name)
    cmd = [
        'ipa', 'dnsrecord-add', dnszone, name, '--a-rec', ip
    ]
    return run_cmd(cmd, name)


def make_groups(*args):
    name = str(next(*args[0]))
    cmd = [
        'ipa', 'group-add', name
    ]
    return run_cmd(cmd, name)


def activate_users(args):
    name = str(next(args[0]))
    pwd = args[1]

    cmd = [
        'ipa', 'passwd', name, PWD_GEN
    ]
    cmd_str = ' '.join(cmd)
    logging.info(f'running: {cmd_str}')

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
    )
    response = process.stdout.readlines()
    if len(response) > 0:
        logging.info(f'{name}: ipa passwd response: {response[1].decode().strip()}')

    cmd = [
        'kinit', '-c', f'/tmp/{name}.cc.tmp', name
    ]
    logging.info('running: ' + ' '.join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        universal_newlines=True
    )
    process.stdin.write(PWD_GEN + '\n')
    process.stdin.write(pwd + '\n')
    process.stdin.write(pwd + '\n')
    process.stdin.close()
    err = process.stderr.read().strip()
    if len(err) > 0:
        return f'failed: {name}: {err}'
    return f'{name}: kinit: successful'


def auth_users(args):
    name = str(next(args[0]))
    pwd = args[1]
    cmd = [
        'kinit', '-c', f'/tmp/{name}.cc.tmp', name
    ]
    logging.info('running: ' + ' '.join(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        universal_newlines=True
    )
    process.stdin.write(pwd + '\n')
    process.stdin.close()
    err = process.stderr.read().strip()
    if len(err) > 0:
        return f'failed: {name}: {err}'
    return f'{name}: kinit: successful'


def run_in_threads(threads, cmd, args, start, stop):    
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        commands = [executor.submit(cmd, args) for _ in range(start, stop)]
        for future in concurrent.futures.as_completed(commands):
            try:
                result = future.result()
                if result is not None:
                    logging.info(f'result: {result}')
                    if result.startswith('failed'):
                        failed.append(result.split(':', maxsplit=2)[1].strip())
            except Exception as exc:
                logging.error('%r generated an exception: %s' % (future, exc))
    return failed


def main():
    start_time = time.time()
    args = sys.argv[1:]

    if (
        (len(args) < 6) or
        (args[0] not in ['usermk', 'groupmk', 'hostmk', 'useract', 'userauth']) or
        (args[0] in ['useract', 'userauth', 'hostmk'] and len(args) < 7) or  # domain or pwd required
        not (all(list(map(lambda x: int(x) >= 0, args[2:6]))))  # start, stop, threads must be positive
        ):
        return help()

    action, name = args[:2]

    logging.basicConfig(
        filename=f'{action}.log',
        filemode='w',
        level=logging.DEBUG,
        format='%(asctime)s: %(levelname)s: %(message)s'
    )

    start, stop, chunk, threads = list(map(int, args[2:6]))
    
    if not 0 < threads < 10:
        threads = 5

    cmds = {
        'usermk': make_users,
        'hostmk': make_hosts,
        'groupmk': make_groups,
        'useract': activate_users,
        'userauth': auth_users,
    }

    names = (name+str(i) for i in range(start, stop+1))

    cmd_args = [names]
    extra_arg = None
    if action in ['useract', 'userauth', 'hostmk']:
        extra_arg = args[6]
        cmd_args.append(extra_arg)
    if action == 'hostmk':
        ips = ('10.10.' + str(0 + i // 256) + '.' + str(0 + i % 256) for i in range(start, stop+1))
        cmd_args.append(ips)
    
    failed_twice = []

    for start_pos in range(start, stop, chunk):

        end_pos = start_pos+chunk
        if end_pos > stop:
            end_pos = stop

        logging.info(f'starting {start_pos}-{end_pos}')

        failed, failed_again = [], []
        failed = run_in_threads(threads, cmds[action], cmd_args, start_pos, end_pos)

        if failed:
            names = ', '.join(failed)
            logging.error(f'Re-running failed jobs for {action}: {names}')
            rerun_args = [(n for n in failed)]
            if extra_arg is not None:
                rerun_args.append(extra_arg)
            failed_again = run_in_threads(
                2, cmds[action], rerun_args, 0, len(failed)
            )  # re-run failed jobs once in 2 threads

        if failed_again:
            failed_twice += failed_again
            names = ', '.join(failed_again)
            logging.error(f'Re-run failed: {action} for: {names}')

        logging.info(f'finished {start_pos}-{end_pos}')

    failed_twice = ', '.join(failed_twice)
    end_time = time.time()
    rounded_end = "{0:.4f}".format(round(end_time - start_time, 4))
    logging.info(f'{action} finished')
    logging.info("Execution time: %s seconds" % (rounded_end))
    if failed_twice:
        logging.critical(f'{action} failed for: {failed_twice}')


if __name__ == "__main__":
    main()

