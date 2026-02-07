import re
from django.core.exceptions import ValidationError


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def validate_cpf(value: str):
    cpf = _only_digits(value)

    if len(cpf) != 11 or cpf == cpf[0] * 11:
        raise ValidationError("CPF inválido.")

    for i in range(9, 11):
        total = sum(int(cpf[num]) * ((i + 1) - num) for num in range(i))
        digit = (total * 10) % 11
        digit = 0 if digit == 10 else digit

        if digit != int(cpf[i]):
            raise ValidationError("CPF inválido.")


def validate_cnpj(value: str):
    cnpj = _only_digits(value)

    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        raise ValidationError("CNPJ inválido.")

    weights_1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    weights_2 = [6] + weights_1

    for weights, pos in ((weights_1, 12), (weights_2, 13)):
        total = sum(int(cnpj[i]) * weights[i] for i in range(len(weights)))
        digit = 11 - (total % 11)
        digit = 0 if digit >= 10 else digit

        if digit != int(cnpj[pos]):
            raise ValidationError("CNPJ inválido.")
