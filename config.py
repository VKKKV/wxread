# config.py 自定义配置,包括阅读次数、推送token的填写
import os
import re

"""
可修改区域
默认使用本地值如果不存在从环境变量中获取值
"""


def _read_num_from_env():
    raw = os.getenv('READ_NUM')
    if raw is None or not raw.strip():
        return 40
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("READ_NUM must be a positive integer") from None


# 阅读次数 默认40次/20分钟
CONFIG_ERROR = None
try:
    READ_NUM = _read_num_from_env()
except ValueError:
    READ_NUM = 40
    CONFIG_ERROR = "READ_NUM must be a positive integer"
# 需要推送时可选，可选pushplus、wxpusher、telegram
PUSH_METHOD = "" or os.getenv('PUSH_METHOD')
PUSHPLUS_TOKEN = "" or os.getenv("PUSHPLUS_TOKEN")
TELEGRAM_BOT_TOKEN = "" or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "" or os.getenv("TELEGRAM_CHAT_ID")
WXPUSHER_SPT = "" or os.getenv("WXPUSHER_SPT")
SERVERCHAN_SPT = "" or os.getenv("SERVERCHAN_SPT")

# read接口的bash命令
curl_str = os.getenv('WXREAD_CURL_BASH')

# 本地部署可填写自己的 Cookie；不提供历史账号/设备示例值。
cookies = {}

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ko;q=0.5',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
}

# 书籍
book = [
    "36d322f07186022636daa5e","6f932ec05dd9eb6f96f14b9","43f3229071984b9343f04a4","d7732ea0813ab7d58g0184b8",
    "3d03298058a9443d052d409","4fc328a0729350754fc56d4","a743220058a92aa746632c0","140329d0716ce81f140468e",
    "1d9321c0718ff5e11d9afe8","ff132750727dc0f6ff1f7b5","e8532a40719c4eb7e851cbe","9b13257072562b5c9b1c8d6"
]

# 章节
chapter = [
    "ecc32f3013eccbc87e4b62e","a87322c014a87ff679a21ea","e4d32d5015e4da3b7fbb1fa","16732dc0161679091c5aeb1",
    "8f132430178f14e45fce0f7","c9f326d018c9f0f895fb5e4","45c322601945c48cce2e120","d3d322001ad3d9446802347",
    "65132ca01b6512bd43d90e3","c20321001cc20ad4d76f5ae","c51323901dc51ce410c121b","aab325601eaab3238922e53",
    "9bf32f301f9bf31c7ff0a60","c7432af0210c74d97b01b1c","70e32fb021170efdf2eca12","6f4322302126f4922f45dec"
]

data = {
    "appId": "wb182564874603h266381671", "b": "ce032b305a9bc1ce0b0dd2a",
    "c": "7f632b502707f6ffaa6bf2e", "ci": 27, "co": 389,
    "sm": "19聚会《三体》网友的聚会地点是一处僻静", "pr": 74, "rt": 15,
    "ts": 1744264311434, "rn": 466,
    "sg": "2b2ec618394b99deea35104168b86381da9f8946d4bc234e062fa320155409fb",
    "ct": 1744264311, "ps": "4ee326507a65a465g015fae",
    "pc": "aab32e207a65a466g010615", "s": "36cc0815"
}


def _decode_ansi(raw):
    """Decode Bash ANSI-C escapes without corrupting literal Unicode."""
    result = bytearray()
    escapes = {'a': '\a', 'b': '\b', 'e': '\x1b', 'E': '\x1b', 'f': '\f',
               'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '\\': '\\', "'": "'", '"': '"', '?': '?'}
    i = 0
    while i < len(raw):
        if raw[i] != '\\':
            result.extend(raw[i].encode('utf-8'))
            i += 1
            continue
        i += 1
        if i == len(raw):
            raise ValueError("Unsupported curl quoting format")
        char = raw[i]
        if char in escapes:
            result.extend(escapes[char].encode('utf-8'))
            i += 1
        elif char in 'xuU' or char in '01234567':
            if char in 'xuU':
                count = {'x': 2, 'u': 4, 'U': 8}[char]
                digits = re.match(r'[0-9a-fA-F]{1,' + str(count) + '}', raw[i + 1:])
                if not digits:
                    raise ValueError("Unsupported curl quoting format")
                value = int(digits.group(), 16)
                i += 1 + len(digits.group())
                if char == 'x':
                    result.append(value)
                else:
                    try:
                        result.extend(chr(value).encode('utf-8'))
                    except (ValueError, UnicodeError):
                        raise ValueError("Unsupported curl quoting format") from None
            else:
                digits = re.match(r'[0-7]{1,3}', raw[i:]).group()
                result.append(int(digits, 8) % 256)
                i += len(digits)
        else:
            raise ValueError("Unsupported curl quoting format")
    try:
        value = result.decode('utf-8')
    except UnicodeError:
        raise ValueError("Unsupported curl quoting format") from None
    if '\x00' in value:
        raise ValueError("Unsupported curl quoting format")
    return value


def _shell_tokens(command):
    """Read a bounded literal Bash-quoting subset, never evaluate shell code.

    Support single/double/ANSI-C quotes, adjacent fragments and LF/CRLF
    continuations (except inside single/ANSI-C quotes). Expansion text stays
    literal, including $'...' inside double quotes. Shell operators outside
    quotes are rejected; this is not a general shell parser. Accept at most
    65536 input characters and 4096 tokens. ANSI-C uses _decode_ansi's strict
    UTF-8 escape subset; unknown escapes and NUL are rejected.
    """
    if not isinstance(command, str) or len(command) > 65536 or '\x00' in command:
        raise ValueError("Unsupported curl quoting format")
    tokens, word, quote, started, i = [], [], None, False, 0
    while i < len(command):
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word.append(char)
            i += 1
        elif char == '\\':
            if i + 1 >= len(command):
                raise ValueError("Unsupported curl quoting format")
            if command.startswith('\\\r\n', i):
                i += 3
            elif command[i + 1] == '\n':
                i += 2
            elif quote == '"' and command[i + 1] not in '$`"\\':
                word.append('\\')
                i += 1
            else:
                word.append(command[i + 1])
                started = True
                i += 2
        elif quote == '"':
            if char == '"':
                quote = None
            else:
                word.append(char)
            i += 1
        elif command.startswith("$'", i):
            # Scan once, preserving escape pairs for the ANSI-C decoder.
            end = i + 2
            while end < len(command) and command[end] != "'":
                end += 2 if command[end] == '\\' else 1
            if end >= len(command):
                raise ValueError("Unsupported curl quoting format")
            word.append(_decode_ansi(command[i + 2:end]))
            started = True
            i = end + 1
        elif char in ("'", '"'):
            quote, started = char, True
            i += 1
        elif char in ' \t\r\n':
            if started:
                tokens.append(''.join(word))
                word, started = [], False
                if len(tokens) > 4096:
                    raise ValueError("Unsupported curl quoting format")
            i += 1
        elif char in ';|&<>()':
            raise ValueError("Unsupported curl quoting format")
        else:
            word.append(char)
            started = True
            i += 1
    if quote is not None:
        raise ValueError("Unsupported curl quoting format")
    if started:
        tokens.append(''.join(word))
    if len(tokens) > 4096:
        raise ValueError("Unsupported curl quoting format")
    return tokens


def _cookie_pairs(value):
    result = {}
    for part in value.split(';'):
        if not part.strip():
            continue
        if '=' not in part:
            raise ValueError("Unsupported cookie format in curl command")
        key, val = part.split('=', 1)
        key, val = key.strip(), val.strip()
        if not key:
            raise ValueError("Unsupported cookie format in curl command")
        result[key] = val
    return result


def convert(curl_command):
    """Extract only explicit -H/--header and -b/--cookie values; never run curl.

    Require a literal curl command. Known value options consume exactly one
    argument even if it looks like -H/-b; attached short and --name=value forms
    are accepted. Other request options/URLs are ignored, not replayed. Reject
    unknown options and short switch clusters rather than guessing their arity.
    -- ends option parsing. No expansion, file reads or shell execution occurs.
    """
    tokens = _shell_tokens(curl_command)
    if not tokens or tokens[0] != 'curl':
        raise ValueError("Unsupported curl command format")
    value_options = {
        '-H', '--header', '-b', '--cookie', '--data-raw', '-d', '--data',
        '--data-binary', '--url', '-X', '--request', '-A', '--user-agent',
        '-e', '--referer',
    }
    switches = {
        '--compressed', '--location', '-L', '--insecure', '-k',
        '--silent', '-s', '--show-error', '-S', '--http1.1', '--http2',
        '--globoff', '-g',
    }
    result_headers, result_cookies = {}, {}
    i = 1
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == '--':
            break
        if token in switches:
            continue
        if not token.startswith('-') or token == '-':
            continue  # Positional URL, never an option argument.
        option, attached = token, None
        if token.startswith('--'):
            if '=' in token:
                option, attached = token.split('=', 1)
        else:
            option = token[:2]
            if len(token) > 2:
                attached = token[2:]
                # Preserve the existing -H=/-b= input contract.
                if option in ('-H', '-b') and attached.startswith('='):
                    attached = attached[1:]
        if option not in value_options:
            raise ValueError("Unsupported curl option")
        if attached is None:
            if i >= len(tokens):
                raise ValueError("Missing curl option argument")
            attached = tokens[i]
            i += 1
        if option in ('-H', '--header'):
            if ':' not in attached:
                raise ValueError("Unsupported curl header format")
            key, value = attached.split(':', 1)
            key, value = key.strip(), value.strip()
            if not key:
                raise ValueError("Unsupported curl header format")
            if key.lower() == 'cookie':
                result_cookies.update(_cookie_pairs(value))
            else:
                result_headers[key] = value
        elif option in ('-b', '--cookie'):
            result_cookies.update(_cookie_pairs(attached))
    return result_headers, result_cookies


if curl_str:
    try:
        headers, cookies = convert(curl_str)
    except ValueError:
        CONFIG_ERROR = "Unsupported curl configuration format"


def validate_config():
    """验证运行所需配置；错误信息不包含任何凭据值。"""
    if CONFIG_ERROR:
        raise ValueError(CONFIG_ERROR)
    for mapping in (headers, cookies):
        for key, value in mapping.items():
            if any(char in str(key) + str(value) for char in ('\r', '\n', '\x00')):
                raise ValueError("Header/cookie contains prohibited control characters")
    if not isinstance(READ_NUM, int) or isinstance(READ_NUM, bool) or READ_NUM <= 0:
        raise ValueError("READ_NUM must be a positive integer")
    for name in ('wr_skey', 'wr_vid'):
        value = cookies.get(name)
        if value is None or not str(value).strip():
            raise ValueError("Required cookie is missing or empty: " + name)
