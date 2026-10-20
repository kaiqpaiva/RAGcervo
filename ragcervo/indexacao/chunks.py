import sys
from pathlib import Path

# limite em caracteres. É uma aproximação: o modelo de embedding conta tokens,
# e depois a gente troca isso por uma contagem de tokens de verdade
TAMANHO_MAXIMO = 1500


def dividir_em_secoes(markdown):
    documento = ""
    secoes = []
    cabecalho_atual = None
    paragrafos_atuais = []

    for linha in markdown.split("\n"):
        linha = linha.strip()

        if linha == "":
            continue

        if linha.startswith("## "):
            # achou um slide novo: guarda o anterior e começa outro
            if cabecalho_atual is not None:
                secoes.append((cabecalho_atual, paragrafos_atuais))
            cabecalho_atual = linha[3:]
            paragrafos_atuais = []
        elif linha.startswith("# "):
            documento = linha[2:]
        else:
            paragrafos_atuais.append(linha)

    # o último slide não tem um "próximo ##" pra fechar ele, então guarda aqui
    if cabecalho_atual is not None:
        secoes.append((cabecalho_atual, paragrafos_atuais))

    return documento, secoes


def dividir_paragrafos(paragrafos, limite):
    partes = []
    parte_atual = []
    tamanho_atual = 0

    for paragrafo in paragrafos:
        estourou = tamanho_atual + len(paragrafo) > limite

        if estourou and len(parte_atual) > 0:
            partes.append(parte_atual)
            parte_atual = []
            tamanho_atual = 0

        parte_atual.append(paragrafo)
        tamanho_atual = tamanho_atual + len(paragrafo)

    if len(parte_atual) > 0:
        partes.append(parte_atual)

    return partes


def criar_chunks(caminho):
    caminho = Path(caminho)
    markdown = caminho.read_text(encoding="utf-8")
    documento, secoes = dividir_em_secoes(markdown)

    chunks = []
    numero = 1

    for cabecalho, paragrafos in secoes:
        partes = dividir_paragrafos(paragrafos, TAMANHO_MAXIMO)

        for parte in partes:
            # o contexto (documento > slide) vai dentro do texto de cada chunk
            texto = f"{documento} > {cabecalho}\n\n" + "\n\n".join(parte)

            chunk = {
                "id": f"{documento}-{numero}",
                "documento": documento,
                "slide": cabecalho,
                "texto": texto,
            }
            chunks.append(chunk)
            numero = numero + 1

    return chunks


if __name__ == "__main__":
    # sem isso, redirecionar a saída (> arquivo, | grep) quebra no Windows com "₂", "≡" etc
    sys.stdout.reconfigure(encoding="utf-8")

    for chunk in criar_chunks(sys.argv[1]):
        print(f"[{chunk['id']}] ({len(chunk['texto'])} caracteres)")
        print(chunk["texto"])
        print("-" * 40)
