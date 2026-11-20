"""Comma/newline lists with CSV quoting for names or organizations containing commas."""
import csv
import io

MAX_CREDITS = 100


def parse_credit_list(value):
    result = []
    try:
        for row in csv.reader(io.StringIO(value or ''), skipinitialspace=True, strict=True):
            for entry in row:
                entry = entry.strip()
                if '\n' in entry or '\r' in entry:
                    raise ValueError('Each individual name or affiliation must be on one line.')
                if entry and entry.casefold() not in {item.casefold() for item in result}:
                    if len(entry) > 350:
                        raise ValueError('Each name or affiliation must be at most 350 characters.')
                    result.append(entry)
                    if len(result) > MAX_CREDITS:
                        raise ValueError(f'Enter at most {MAX_CREDITS} names or affiliations.')
    except csv.Error as error:
        raise ValueError('Use comma-separated values. Put a value containing a comma in double quotes.') from error
    return result


def format_credit_list(values):
    if isinstance(values, str):
        values = [line.strip() for line in values.splitlines() if line.strip()]
    stream = io.StringIO()
    csv.writer(stream, lineterminator='').writerow(values)
    return stream.getvalue()


def source_credit_list(value):
    try:
        return parse_credit_list(value)
    except ValueError:
        # An ambiguous declaration stays literal; do not invent identities.
        return [value]
