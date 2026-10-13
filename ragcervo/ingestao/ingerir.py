import sys
from pathlib import Path

from ragcervo.ingestao.conversor_pptx import converter_arquivo as converter_pptx

# extensão do arquivo -> função que converte esse tipo
CONVERSORES = {
    ".pptx": converter_pptx,
}

def ingerir_pasta(pasta_entrada, pasta_saida):
    pasta_entrada = Path(pasta_entrada)
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)

    convertidos = 0 
    erros = 0

    for arquivo in sorted(pasta_entrada.iterdir()):
        extensao = arquivo.suffix.lower()

        if arquivo.name.startswith("~$"):
            continue  # arquivo temporário que o PowerPoint cria com o original aberto

        if extensao not in CONVERSORES:
            print(f"pulado (formato não suportado): {arquivo.name}")
            continue

        conversor = CONVERSORES[extensao]
        destino = pasta_saida / f"{arquivo.stem}.md"

        try:
            markdown = conversor(arquivo)
            destino.write_text(markdown, encoding="utf-8")
            print(f"ok: {arquivo.name}")
            convertidos = convertidos + 1
        except Exception as erro:
            print(f"erro em {arquivo.name}: {erro}")
            erros = erros + 1

    print(f"\n{convertidos} convertido(s), {erros} erro(s)")


if __name__ == "__main__":
    ingerir_pasta(sys.argv[1], sys.argv[2])