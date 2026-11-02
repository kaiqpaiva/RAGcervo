import re
import sys
from pathlib import Path

# limite em caracteres. É uma aproximação: o modelo de embedding conta tokens,
# e depois a gente troca isso por uma contagem de tokens de verdade
TAMANHO_MAXIMO = 1500

IMAGEM = re.compile(r"!\[.*?\]\((.*?)\)")


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
        elif linha.startswith("|") and len(paragrafos_atuais) > 0 and paragrafos_atuais[-1].startswith("|"):
            # as linhas de uma tabela ficam juntas em um parágrafo só
            paragrafos_atuais[-1] = paragrafos_atuais[-1] + "\n" + linha
        else:
            paragrafos_atuais.append(linha)

    # o último slide não tem um "próximo ##" pra fechar ele, então guarda aqui
    if cabecalho_atual is not None:
        secoes.append((cabecalho_atual, paragrafos_atuais))

    return documento, secoes


def dividir_tabelas(paragrafos, limite):
    # tabela maior que o limite vira várias, cada uma repetindo o cabeçalho
    resultado = []

    for paragrafo in paragrafos:
        if not paragrafo.startswith("|") or len(paragrafo) <= limite:
            resultado.append(paragrafo)
            continue

        linhas = paragrafo.split("\n")
        cabecalho = linhas[:2]
        atuais = []

        for linha in linhas[2:]:
            if len("\n".join(cabecalho + atuais + [linha])) > limite and len(atuais) > 0:
                resultado.append("\n".join(cabecalho + atuais))
                atuais = []
            atuais.append(linha)

        resultado.append("\n".join(cabecalho + atuais))

    return resultado


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
        partes = dividir_paragrafos(dividir_tabelas(paragrafos, TAMANHO_MAXIMO), TAMANHO_MAXIMO)

        for parte in partes:
            # imagem não entra no embedding: o caminho fica guardado à parte
            imagens = [IMAGEM.fullmatch(p).group(1) for p in parte if IMAGEM.fullmatch(p)]
            textos = [p for p in parte if not IMAGEM.fullmatch(p)]

            # o contexto (documento > slide) vai dentro do texto de cada chunk
            texto = f"{documento} > {cabecalho}\n\n" + "\n\n".join(textos)

            chunk = {
                "id": f"{documento}-{numero}",
                "documento": documento,
                "slide": cabecalho,
                "texto": texto,
                "imagens": imagens,
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
