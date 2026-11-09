"""Read the data-only MySQL export without executing any SQL.

This intentionally accepts a small, explicit mysqldump grammar. SQL expressions,
DDL and unknown tables are errors, never statements passed to a database.
"""
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


TABLE_COLUMNS = {
    "cancelamentos_cancelamento": "id ticket_arion designacao_circuito cod_cliente protocolo isp contato_isp cliente cliente_final fila status observacao agendamento data_criacao_chamado criado_em atualizado_em valor_multa_rescisao valor_equipamento atendente_id apropriado_em bairro canal_contato cep cidade complemento confirmacao_sem_nova_cobranca data_baixa_financeira data_cancelamento_confirmado data_primeiro_contato data_proxima_acao data_ultima_interacao endereco numero observacao_financeira proxima_acao responsavel_id ticket_url uf ultima_competencia_faturada valor_outros concluido_em".split(),
    "cancelamentos_historicocancelamento": "id tipo descricao criado_em cancelamento_id realizado_por_id".split(),
    "cancelamentos_historicofila": "id fila_origem fila_destino movido_em cancelamento_id movido_por_id".split(),
    "cancelamentos_observacaocancelamento": "id texto criado_em cancelamento_id criado_por_id".split(),
    "cancelamentos_pendenciacancelamento": "id tipo descricao valor status data_prevista resolvida_em criado_em cancelamento_id criada_por_id".split(),
}
MAIN = "cancelamentos_cancelamento"
PREFIX = "cancelamentos_"
MAX_BYTES = 64 * 1024 * 1024


class DumpError(ValueError):
    """Only structural diagnostics are safe to display; never include row text."""


def statements(text):
    quote, escaped, index = False, False, 0
    buffer = []
    while index < len(text):
        char = text[index]
        if quote:
            buffer.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    buffer.append("'")
                    index += 1
                else:
                    quote = False
        elif char == "'":
            quote = True
            buffer.append(char)
        elif text.startswith("--", index) and (index + 2 == len(text) or text[index + 2].isspace()):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            buffer.append(" ")
        elif char == "#":
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            buffer.append(" ")
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise DumpError("Comentário SQL incompleto.")
            comment = text[index + 2:end]
            # Executable comments in this export contain only mysqldump setup.
            if comment.startswith("!"):
                command = re.sub(r"^!\d*\s*", "", comment).strip()
                if not re.fullmatch(r"(?:SET\s+[^;]+|ALTER TABLE `cancelamentos_[a-z]+` (?:DISABLE|ENABLE) KEYS)\s*", command, re.I):
                    raise DumpError("Comentário executável fora do contrato de exportação.")
                if "NO_BACKSLASH_ESCAPES" in command.upper():
                    raise DumpError("SQL mode incompatível com o leitor de escapes.")
                if command.upper().startswith("SET TIME_ZONE") and "'+00:00'" not in command and command.upper() != "SET TIME_ZONE=@OLD_TIME_ZONE":
                    raise DumpError("O dump precisa declarar TIME_ZONE UTC.")
            buffer.append(" ")
            index = end + 1
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                yield statement
            buffer = []
        else:
            buffer.append(char)
        index += 1
    if quote:
        raise DumpError("String SQL incompleta.")
    if "".join(buffer).strip():
        raise DumpError("Instrução SQL sem terminador.")


def value_rows(text):
    index, size = 0, len(text)
    # MySQL retains \% and \_ outside LIKE; see its String Literals contract.
    escapes = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "%": "\\%", "_": "\\_"}

    def space():
        nonlocal index
        while index < size and text[index].isspace():
            index += 1

    while index < size:
        space()
        if index >= size or text[index] != "(":
            raise DumpError("Tupla INSERT inválida.")
        index += 1
        values = []
        while True:
            space()
            if index < size and text[index] == "'":
                index += 1
                chars, closed = [], False
                while index < size:
                    char = text[index]
                    index += 1
                    if char == "\\":
                        if index == size:
                            raise DumpError("Escape SQL incompleto.")
                        char = text[index]
                        chars.append(escapes.get(char, char))
                        index += 1
                    elif char == "'":
                        if index < size and text[index] == "'":
                            chars.append("'")
                            index += 1
                        else:
                            closed = True
                            break
                    else:
                        chars.append(char)
                if not closed:
                    raise DumpError("String SQL incompleta.")
                value = "".join(chars)
            else:
                end = index
                while end < size and text[end] not in ",)":
                    end += 1
                literal = text[index:end].strip()
                if literal == "NULL":
                    value = None
                elif re.fullmatch(r"-?\d+", literal):
                    value = int(literal)
                elif re.fullmatch(r"-?\d+\.\d+", literal):
                    value = literal  # Preserve Decimal precision, never float.
                else:
                    raise DumpError("Expressão SQL não permitida em VALUES.")
                index = end
            values.append(value)
            space()
            if index >= size or text[index] not in ",)":
                raise DumpError("Separador de campo inválido.")
            separator = text[index]
            index += 1
            if separator == ")":
                break
        yield values
        space()
        if index == size:
            return
        if text[index] != ",":
            raise DumpError("Separador de registros inválido.")
        index += 1
        space()
        if index == size:
            raise DumpError("INSERT com registro ausente.")


def read_dump(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise DumpError("Exportação ausente ou maior que o limite de 64 MiB.")
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        raise DumpError("A exportação deve estar em UTF-8.") from None
    if "SET TIME_ZONE='+00:00'" not in text:
        raise DumpError("Exportação sem declaração de fuso UTC.")
    tables = {name: [] for name in TABLE_COLUMNS}
    seen = set()
    for statement in statements(text):
        match = re.fullmatch(r"INSERT INTO `([a-z_]+)`\s*\(([^)]+)\)\s*VALUES\s*(.+)", statement, re.S)
        if not match:
            if re.fullmatch(r"LOCK TABLES `cancelamentos_[a-z]+` WRITE|UNLOCK TABLES", statement):
                continue
            raise DumpError("Instrução não permitida no dump somente de dados.")
        table, columns_text, values_text = match.groups()
        if table not in TABLE_COLUMNS:
            raise DumpError("Tabela fora do escopo de Cancelamentos.")
        if not re.fullmatch(r"`[a-z_]+`(?:\s*,\s*`[a-z_]+`)*", columns_text):
            raise DumpError("Identificadores de colunas inválidos.")
        columns = re.findall(r"`([a-z_]+)`", columns_text)
        if len(set(columns)) != len(columns) or set(columns) != set(TABLE_COLUMNS[table]):
            raise DumpError(f"Contrato de colunas divergente: {table}.")
        seen.add(table)
        ids = {row["id"] for row in tables[table]}
        for values in value_rows(values_text):
            if len(values) != len(columns):
                raise DumpError(f"Quantidade de valores divergente: {table}.")
            row = dict(zip(columns, values))
            if not isinstance(row["id"], int) or row["id"] <= 0 or row["id"] in ids:
                raise DumpError(f"PK inválida ou duplicada: {table}.")
            ids.add(row["id"])
            tables[table].append(row)
    if seen != set(TABLE_COLUMNS):
        raise DumpError("Exportação incompleta: são necessárias as cinco tabelas.")
    parents = {row["id"] for row in tables[MAIN]}
    for table, rows in tables.items():
        if table != MAIN and any(row["cancelamento_id"] not in parents for row in rows):
            raise DumpError(f"Referência órfã: {table}.")
    return tables, hashlib.sha256(content).hexdigest()


def validate_schema(path, tables):
    """Read DDL as metadata only. DROP/CREATE are never executed."""
    content = Path(path).expanduser().resolve(strict=True).read_bytes()
    if len(content) > MAX_BYTES:
        raise DumpError("Schema acima do limite de leitura.")
    definitions = {}
    for statement in statements(content.decode("utf-8-sig")):
        if re.fullmatch(r"DROP TABLE IF EXISTS `cancelamentos_[a-z]+`", statement):
            continue
        match = re.fullmatch(r"CREATE TABLE `([a-z_]+)`\s*\((.+)\) ENGINE=InnoDB\b.+", statement, re.S)
        if not match or match[1] not in TABLE_COLUMNS or match[1] in definitions:
            raise DumpError("DDL fora do contrato das cinco tabelas.")
        fields = {}
        for line in match[2].splitlines():
            line = line.strip()
            if not line.startswith("`"):
                continue
            column = re.fullmatch(r"`([a-z_]+)` (bigint|int|tinyint\(1\)|varchar\(\d+\)|longtext|date|datetime\(6\)|decimal\(12,2\)) (.+?),?", line)
            if not column:
                raise DumpError(f"Tipo SQL não suportado: {match[1]}.")
            fields[column[1]] = (column[2], "NOT NULL" not in column[3])
        if set(fields) != set(TABLE_COLUMNS[match[1]]):
            raise DumpError(f"Schema e exportação divergem: {match[1]}.")
        definitions[match[1]] = fields
    if set(definitions) != set(TABLE_COLUMNS):
        raise DumpError("Schema incompleto.")
    for table, rows in tables.items():
        for row in rows:
            for field, (kind, nullable) in definitions[table].items():
                value = row[field]
                try:
                    if value is None:
                        if not nullable:
                            raise ValueError
                    elif kind.startswith("varchar") or kind == "longtext":
                        if not isinstance(value, str) or (kind.startswith("varchar") and len(value) > int(re.search(r"\d+", kind)[0])):
                            raise ValueError
                    elif kind == "date":
                        date.fromisoformat(value)
                    elif kind == "datetime(6)":
                        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d{1,6})?", value):
                            raise ValueError
                        datetime.fromisoformat(value)
                    elif kind == "decimal(12,2)":
                        number = Decimal(str(value))
                        if not number.is_finite() or abs(number) >= Decimal("10000000000") or number != number.quantize(Decimal("0.01")):
                            raise ValueError
                    elif not isinstance(value, int) or (kind == "tinyint(1)" and value not in (0, 1)) or abs(value) >= 2 ** (63 if kind == "bigint" else 31):
                        raise ValueError
                except (ValueError, TypeError, InvalidOperation):
                    raise DumpError(f"Valor incompatível com o schema: {table}/{row['id']}/{field}.") from None
    return hashlib.sha256(content).hexdigest()
