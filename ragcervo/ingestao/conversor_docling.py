import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

from docling.backend.mspowerpoint_backend import MsPowerpointDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    PowerpointFormatOption,
)
from docling_core.types.doc import DocItemLabel, PictureItem, TableItem
from docling_core.types.doc.common.meta import DescriptionMetaField
from docling_core.types.doc.document import ContentLayer
from docling_core.types.doc.items.picture.meta import PictureMeta
import numpy
from PIL import Image
from pptx.oxml.ns import qn
from rapidocr import EngineType, RapidOCR

EXTENSOES = {
    ".pptx": InputFormat.PPTX,
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
}

# imagens menores que isso são ícones decorativos e não valem ser guardadas
LADO_MINIMO_IMAGEM = 300

MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
OMATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"

# letras itálicas de PDF ("𝐴𝑛", "𝜇", "ℎ"), que a busca não casa com "An", "μ", "h"
LETRAS_MATEMATICAS = re.compile(r"[\U0001D400-\U0001D7FF\u210E]")

# o conversor de equações do Docling embrulha as setas como texto
SETA_COMO_TEXTO = re.compile(r"\\text\{\s*\\text(right|left)arrow\s*\}")

# ferramentas costumam gravar o nome do arquivo como texto alternativo
ALT_SO_COM_ARQUIVO = re.compile(r".*\.(png|jpe?g|gif|svg|webp|bmp)", re.IGNORECASE)

CONFIANCA_MINIMA_OCR = 0.8

# descarta o que o OCR acha em desenhos, como setas e bolinhas lidas como "●"
TEM_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÿ]{2}")


def expor_equacoes(slide):
    # o PowerPoint grava cada equação dentro de um mc:AlternateContent, que o
    # python-pptx não enxerga como shape. Sem isso a equação some da extração
    arvore = slide.shapes._spTree

    for alternativa in list(arvore.iter(f"{MC}AlternateContent")):
        escolha = alternativa.find(f"{MC}Choice")
        if escolha is not None and next(escolha.iter(OMATH), None) is not None:
            alternativa.getparent().replace(alternativa, escolha[0])


def remover_numero_do_slide(slide):
    # caixas de texto comuns com o campo de número não são filtradas pelo Docling
    arvore = slide.shapes._spTree

    for campo in list(arvore.iter(qn("a:fld"))):
        if campo.get("type") == "slidenum":
            shape = next(campo.iterancestors(qn("p:sp")))
            shape.getparent().remove(shape)


def texto_alternativo(shape):
    valores = shape._element.xpath("./*/p:cNvPr/@descr")

    if len(valores) == 0 or valores[0].strip() == "" or ALT_SO_COM_ARQUIVO.fullmatch(valores[0].strip()):
        return None

    return valores[0].strip()


class BackendPptxOrdemOriginal(MsPowerpointDocumentBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for slide in self.pptx_obj.slides:
            expor_equacoes(slide)
            remover_numero_do_slide(slide)

    # o Docling ordena as shapes por linha visual, o que intercala as colunas
    # dos slides ("Luz solar / Glicose / Água / Oxigênio"). Na ordem do arquivo
    # cada coluna fica junta. Método privado: rever ao atualizar o Docling
    def _iter_shapes_by_position(self, shapes):
        return iter(shapes)

    # o Docling descarta o texto alternativo: guarda ele como descrição da imagem
    def _handle_pictures(self, shape, parent_slide, slide_ind, doc, slide_size, fill_rid=None):
        antes = len(doc.pictures)
        super()._handle_pictures(shape, parent_slide, slide_ind, doc, slide_size, fill_rid)

        alt = texto_alternativo(shape)
        if alt is not None and len(doc.pictures) > antes:
            doc.pictures[-1].meta = PictureMeta(description=DescriptionMetaField(text=alt))


@lru_cache(maxsize=1)
def criar_conversor():  # só uma vez: carregar os modelos do PDF é demorado
    opcoes_pdf = PdfPipelineOptions(
        do_formula_enrichment=True,  # fórmula vira LaTeX
        generate_picture_images=True,
        images_scale=2,
    )

    return DocumentConverter(
        allowed_formats=list(EXTENSOES.values()),
        format_options={
            InputFormat.PPTX: PowerpointFormatOption(backend=BackendPptxOrdemOriginal),
            InputFormat.PDF: PdfFormatOption(pipeline_options=opcoes_pdf),
        },
    )


@lru_cache(maxsize=1)
def criar_ocr():  # usa o torch, que já vem com o Docling
    motor = EngineType.TORCH
    return RapidOCR(params={"Det.engine_type": motor, "Cls.engine_type": motor, "Rec.engine_type": motor})


def ler_texto_da_imagem(caminho):
    with Image.open(caminho) as imagem:
        resultado = criar_ocr()(numpy.array(imagem.convert("RGB")))

    if resultado.txts is None:
        return []

    return [
        texto
        for texto, confianca in zip(resultado.txts, resultado.scores)
        if confianca >= CONFIANCA_MINIMA_OCR and TEM_PALAVRA.search(texto)
    ]


def limpar_texto(texto):
    return LETRAS_MATEMATICAS.sub(lambda letra: unicodedata.normalize("NFKC", letra.group()), texto).strip()


def numero_da_pagina(item):
    if len(item.prov) == 0:
        return 1  # docx não tem páginas: fica tudo em uma seção só

    return item.prov[0].page_no


def salvar_imagem(item, documento, pasta_imagens, nome):
    imagem = item.get_image(documento)

    if imagem is None or max(imagem.size) < LADO_MINIMO_IMAGEM:
        return None

    pasta_imagens.mkdir(parents=True, exist_ok=True)
    destino = pasta_imagens / f"{nome}.png"
    imagem.save(destino)

    return destino


def blocos_da_imagem(item, destino, com_ocr):
    blocos = [f"![imagem]({destino.as_posix()})"]

    if item.meta is not None and item.meta.description is not None:
        blocos.append(f"Descrição da imagem: {limpar_texto(item.meta.description.text)}")

    textos = ler_texto_da_imagem(destino) if com_ocr else []
    if len(textos) > 0:
        blocos.append("Texto na imagem: " + "; ".join(textos))

    return blocos


def agrupar_por_pagina(documento, pasta_imagens, com_ocr):
    paginas = {}  # número da página -> {"titulo": ..., "blocos": [...]}
    numero_imagem = 1

    camadas = {ContentLayer.BODY, ContentLayer.NOTES}

    for item, _ in documento.iterate_items(included_content_layers=camadas):
        pagina = paginas.setdefault(numero_da_pagina(item), {"titulo": None, "blocos": []})

        if isinstance(item, PictureItem) and item.meta is not None and item.meta.tabular_chart is not None:
            # gráfico nativo: os dados vêm como tabela. O split tira o aviso de imagem ausente
            # e o nome do tipo em inglês ("Line chart"), que vem antes da tabela
            dados = item.export_to_markdown(documento).split("-->")[-1].split("\n\n")[-1]
            pagina["blocos"].extend(["Dados do gráfico:", limpar_texto(dados)])
        elif isinstance(item, PictureItem):
            destino = salvar_imagem(item, documento, pasta_imagens, f"imagem-{numero_imagem}")
            numero_imagem = numero_imagem + 1
            if destino is not None:
                pagina["blocos"].extend(blocos_da_imagem(item, destino, com_ocr))
        elif isinstance(item, TableItem):
            # export_to_markdown repetiria a legenda, que já vem como item próprio
            tabela = item.export_to_dataframe(documento).to_markdown(index=False)
            pagina["blocos"].append(limpar_texto(tabela))
        elif item.text.strip() == "":
            continue
        elif item.content_layer == ContentLayer.NOTES:
            pagina["blocos"].append(f"Notas: {limpar_texto(item.text)}")
        elif item.label == DocItemLabel.TITLE and pagina["titulo"] is None:
            pagina["titulo"] = limpar_texto(item.text)
        elif item.label == DocItemLabel.FORMULA:
            formula = SETA_COMO_TEXTO.sub(r"\\\1arrow ", item.text.strip())
            pagina["blocos"].append(f"$$ {formula} $$")
        elif item.label == DocItemLabel.LIST_ITEM:
            pagina["blocos"].append(f"- {limpar_texto(item.text)}")
        else:
            pagina["blocos"].append(limpar_texto(item.text))

    return paginas


def primeiro_texto(blocos):  # plano B de slide sem título: ignora as imagens
    for indice, bloco in enumerate(blocos):
        if not bloco.startswith("!["):
            return blocos.pop(indice)

    return None


def converter_arquivo(caminho, pasta_imagens):
    caminho = Path(caminho)
    documento = criar_conversor().convert(caminho).document
    e_slide = caminho.suffix.lower() == ".pptx"
    com_ocr = caminho.suffix.lower() != ".pdf"  # o pipeline de PDF já tem OCR próprio
    paginas = agrupar_por_pagina(documento, Path(pasta_imagens) / caminho.stem, com_ocr)

    rotulo = "Slide" if e_slide else "Página"

    partes = [f"# {caminho.stem}"]
    for numero in sorted(paginas):
        titulo = paginas[numero]["titulo"]
        blocos = paginas[numero]["blocos"]

        if titulo is None and e_slide:
            titulo = primeiro_texto(blocos)

        if titulo is None:
            partes.append(f"## {rotulo} {numero}")
        else:
            partes.append(f"## {rotulo} {numero}: {titulo}")

        partes.extend(blocos)

    return "\n\n".join(partes) + "\n"


if __name__ == "__main__":
    origem = Path(sys.argv[1])
    destino = Path("dados") / "markdown" / f"{origem.stem}.md"

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(converter_arquivo(origem, Path("dados") / "imagens"), encoding="utf-8")

    print(f"salvo em {destino}")
