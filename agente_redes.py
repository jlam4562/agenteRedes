#!/usr/bin/env python3
"""
Agente de contenido para redes (LinkedIn y X).

- Lunes  -> tema de Ciencias de la Computación (CS)
- Jueves -> tema de Sistemas de Información Gerenciales (MIS)

Busca SOLO en dominios oficiales, valida que las fuentes citadas realmente
fueron consultadas, y envía por correo: copy de LinkedIn, post de X,
carrusel y propuestas de imagen.

Uso:
    python agente_redes.py            # decide por día de la semana
    python agente_redes.py CS         # fuerza CS (pruebas)
    python agente_redes.py MIS --dry-run   # no envía; guarda vista previa HTML
"""
import json
import os
import re
import smtplib
import sys
import datetime as dt
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import anthropic

DESTINATARIO = "jlasenci@espol.edu.ec"
MODELO = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
TZ = ZoneInfo("America/Guayaquil")
HISTORIAL = Path(__file__).with_name("historial.json")

NOMBRES = {
    "CS": "Ciencias de la Computación",
    "MIS": "Sistemas de Información Gerenciales",
}

ENFOQUE = {
    "CS": (
        "inteligencia artificial y agentes, ciberseguridad, computación cuántica, "
        "ingeniería de software, sistemas distribuidos, estándares y avances de "
        "investigación"
    ),
    "MIS": (
        "ERP y sistemas empresariales, analítica y gobierno de datos, IA aplicada a "
        "la gestión, transformación digital, ciberseguridad y riesgo empresarial, "
        "decisiones basadas en datos"
    ),
}

# Edita estas listas para agregar o quitar fuentes oficiales.
DOMINIOS = {
    "CS": [
        "acm.org", "ieee.org", "computer.org", "nist.gov", "nsf.gov", "cisa.gov",
        "nature.com", "science.org", "research.google", "blog.google",
        "deepmind.google", "microsoft.com", "aws.amazon.com", "ibm.com",
        "openai.com", "anthropic.com", "nvidia.com", "w3.org", "ietf.org",
        "owasp.org", "github.blog", "linuxfoundation.org",
    ],
    "MIS": [
        "aisnet.org", "misq.umn.edu", "sloanreview.mit.edu", "hbr.org",
        "gartner.com", "deloitte.com", "mckinsey.com", "forrester.com",
        "weforum.org", "oecd.org", "worldbank.org", "iso.org", "isaca.org",
        "sap.com", "oracle.com", "microsoft.com", "salesforce.com", "ibm.com",
        "aws.amazon.com", "cepal.org", "iadb.org", "itu.int", "un.org",
    ],
}

SISTEMA = """Eres un editor de contenido técnico para redes profesionales.
Reglas estrictas:
1. Usa ÚNICAMENTE información que encontraste con la búsqueda web en esta sesión.
   No agregues cifras, fechas ni afirmaciones de memoria.
2. Cada dato clave debe tener su fuente (URL exacta consultada).
3. Prioriza novedades de los últimos 7 días; si no hay, amplía a 30 días.
4. Si no hay evidencia suficiente en fuentes oficiales, responde con
   {"sin_tema": true, "motivo": "..."} en lugar de inventar.
5. Redacta en español, tono profesional y cercano, sin exageraciones ni
   promesas no respaldadas por las fuentes.
6. No copies texto literal de las fuentes: parafrasea.
7. Responde SOLO con un objeto JSON válido, sin texto adicional ni markdown."""

ESQUEMA = """{
  "tema": "título corto del tema",
  "resumen": "2-3 frases con lo ocurrido",
  "por_que_importa": "1-2 frases sobre la relevancia para estudiantes y profesionales",
  "fuentes": [{"titulo": "", "url": "", "fecha": "AAAA-MM-DD o texto", "dato_clave": ""}],
  "linkedin": {"copy": "150-220 palabras, con gancho inicial, saltos de línea y pregunta final", "hashtags": ["#..."]},
  "x": {"post": "máximo 270 caracteres, incluye la fuente principal", "hilo": ["opcional: 2-4 posts de máximo 270 caracteres"]},
  "carrusel": [{"n": 1, "titulo": "", "texto": "máx. 25 palabras", "visual": "qué mostrar"}],
  "imagenes": [{"formato": "1080x1080 | 1200x675 | 1080x1350", "descripcion": "", "prompt": "prompt detallado para generador de imágenes, sin texto incrustado ni logos de terceros"}],
  "notas_verificacion": "qué se verificó y qué conviene revisar antes de publicar"
}"""


# ----------------------------------------------------------------- utilidades
def cargar_historial():
    try:
        return json.loads(HISTORIAL.read_text(encoding="utf-8"))
    except Exception:
        return []


def guardar_historial(hist, categoria, tema, urls):
    hist.append({
        "fecha": dt.datetime.now(TZ).strftime("%Y-%m-%d"),
        "categoria": categoria,
        "tema": tema,
        "urls": urls,
    })
    HISTORIAL.write_text(
        json.dumps(hist[-60:], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def dominio_permitido(url, permitidos):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in permitidos)


def elegir_categoria(args):
    for a in args:
        if a.upper() in ("CS", "MIS"):
            return a.upper()
    dia = dt.datetime.now(TZ).weekday()  # 0 = lunes, 3 = jueves
    if dia == 0:
        return "CS"
    if dia == 3:
        return "MIS"
    print("Hoy no es lunes ni jueves. Usa: python agente_redes.py CS|MIS")
    sys.exit(0)


# ------------------------------------------------------------------ consulta
def consultar(categoria, hist):
    hoy = dt.datetime.now(TZ).strftime("%d de %B de %Y")
    evitar = [h["tema"] for h in hist if h.get("categoria") == categoria][-10:]
    prompt = (
        f"Hoy es {hoy}. Busca el tema más relevante y reciente en "
        f"{NOMBRES[categoria]} ({ENFOQUE[categoria]}) con foco en tendencias de la "
        f"industria. Evita repetir estos temas ya publicados: {evitar or 'ninguno'}.\n\n"
        f"Entrega el resultado con esta estructura JSON:\n{ESQUEMA}"
    )
    herramientas = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 8,
        "allowed_domains": DOMINIOS[categoria],
    }]
    cliente = anthropic.Anthropic()
    mensajes = [{"role": "user", "content": prompt}]
    resp = None
    for _ in range(5):
        resp = cliente.messages.create(
            model=MODELO,
            max_tokens=6000,
            system=SISTEMA,
            tools=herramientas,
            messages=mensajes,
        )
        if resp.stop_reason == "pause_turn":
            mensajes.append({"role": "assistant", "content": resp.content})
            continue
        break
    return resp


def extraer(resp):
    texto = "".join(b.text for b in resp.content if b.type == "text")
    ini, fin = texto.find("{"), texto.rfind("}")
    if ini == -1 or fin == -1:
        raise ValueError("La respuesta no contiene JSON.")
    datos = json.loads(texto[ini:fin + 1])

    consultadas = set()
    for b in resp.content:
        if b.type == "web_search_tool_result" and isinstance(b.content, list):
            for r in b.content:
                url = getattr(r, "url", None)
                if url:
                    consultadas.add(url)
    return datos, consultadas


def validar_fuentes(datos, consultadas, categoria):
    """Conserva solo fuentes oficiales que el agente realmente consultó."""
    buenas = []
    for f in datos.get("fuentes", []):
        url = f.get("url", "")
        if url in consultadas and dominio_permitido(url, DOMINIOS[categoria]):
            buenas.append(f)
    return buenas


# --------------------------------------------------------------------- correo
def construir_correo(categoria, d, fuentes):
    li, x = d.get("linkedin", {}), d.get("x", {})
    post_x = x.get("post", "")
    aviso_x = ""
    if len(post_x) > 280:
        aviso_x = f" (¡{len(post_x)} caracteres! acórtalo)"

    # Texto plano
    t = [f"TEMA ({categoria}): {d['tema']}", "", d.get("resumen", ""), "",
         "POR QUÉ IMPORTA: " + d.get("por_que_importa", ""), "", "FUENTES OFICIALES:"]
    t += [f"- {f.get('titulo','')} ({f.get('fecha','')}): {f['url']}" for f in fuentes]
    t += ["", "--- COPY LINKEDIN ---", li.get("copy", ""), " ".join(li.get("hashtags", [])),
          "", f"--- POST X ({len(post_x)} car.) ---", post_x]
    for i, p in enumerate(x.get("hilo", []), 1):
        t.append(f"Hilo {i}: {p}")
    t += ["", "--- CARRUSEL ---"]
    for s in d.get("carrusel", []):
        t.append(f"{s.get('n')}. {s.get('titulo')}: {s.get('texto')} [Visual: {s.get('visual')}]")
    t += ["", "--- IMÁGENES ---"]
    for im in d.get("imagenes", []):
        t.append(f"[{im.get('formato')}] {im.get('descripcion')}\nPrompt: {im.get('prompt')}")
    t += ["", "NOTAS DE VERIFICACIÓN: " + d.get("notas_verificacion", "")]
    texto = "\n".join(t)

    # HTML
    def p(s):
        return escape(s).replace("\n", "<br>")

    caja = "background:#f5f7fa;border-left:4px solid #0a66c2;padding:12px;margin:8px 0;"
    h = [f"<div style='font-family:Arial,sans-serif;max-width:680px;line-height:1.5'>",
         f"<h2>{escape(d['tema'])} <small style='color:#666'>({categoria})</small></h2>",
         f"<p>{p(d.get('resumen',''))}</p>",
         f"<p><b>Por qué importa:</b> {p(d.get('por_que_importa',''))}</p>",
         "<h3>Fuentes oficiales verificadas</h3><ul>"]
    for f in fuentes:
        h.append(
            f"<li><a href='{escape(f['url'])}'>{escape(f.get('titulo') or f['url'])}</a> "
            f"— {escape(f.get('fecha',''))}<br><small>{escape(f.get('dato_clave',''))}</small></li>"
        )
    h.append("</ul>")
    h.append(f"<h3>Copy para LinkedIn</h3><div style='{caja}'>{p(li.get('copy',''))}"
             f"<br><br>{escape(' '.join(li.get('hashtags', [])))}</div>")
    h.append(f"<h3>Post para X ({len(post_x)} caracteres{escape(aviso_x)})</h3>"
             f"<div style='{caja}'>{p(post_x)}</div>")
    for i, hp in enumerate(x.get("hilo", []), 1):
        h.append(f"<div style='{caja}'><b>Hilo {i}:</b> {p(hp)}</div>")
    h.append("<h3>Propuesta de carrusel</h3><ol>")
    for s in d.get("carrusel", []):
        h.append(
            f"<li><b>{escape(s.get('titulo',''))}</b><br>{escape(s.get('texto',''))}"
            f"<br><i>Visual: {escape(s.get('visual',''))}</i></li>"
        )
    h.append("</ol><h3>Propuestas de imagen</h3>")
    for im in d.get("imagenes", []):
        h.append(
            f"<div style='{caja}'><b>{escape(im.get('formato',''))}</b> — "
            f"{escape(im.get('descripcion',''))}<br><small><b>Prompt:</b> "
            f"{escape(im.get('prompt',''))}</small></div>"
        )
    h.append(f"<h3>Notas de verificación</h3><p>{p(d.get('notas_verificacion',''))}</p>"
             "<p style='color:#888;font-size:12px'>Revisa los datos contra las fuentes "
             "antes de publicar.</p></div>")
    return texto, "".join(h)


def enviar(asunto, texto, html, dry_run=False):
    if dry_run:
        Path("vista_previa.html").write_text(html, encoding="utf-8")
        print(f"[dry-run] Asunto: {asunto}\nVista previa guardada en vista_previa.html")
        return
    usuario = os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = usuario
    msg["To"] = DESTINATARIO
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    puerto = int(os.getenv("SMTP_PORT", "465"))
    if puerto == 465:
        with smtplib.SMTP_SSL(host, puerto) as s:
            s.login(usuario, os.environ["SMTP_PASS"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, puerto) as s:
            s.starttls()
            s.login(usuario, os.environ["SMTP_PASS"])
            s.send_message(msg)
    print("Correo enviado a", DESTINATARIO)


# ----------------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    categoria = elegir_categoria(args)
    asunto = f"Tema para publicar en redes - {categoria}"

    hist = cargar_historial()
    resp = consultar(categoria, hist)
    datos, consultadas = extraer(resp)

    if datos.get("sin_tema"):
        aviso = f"No se encontró un tema verificable en fuentes oficiales: {datos.get('motivo','')}"
        enviar(asunto + " (sin tema)", aviso, f"<p>{escape(aviso)}</p>", dry)
        return

    fuentes = validar_fuentes(datos, consultadas, categoria)
    if len(fuentes) < 2:
        aviso = (
            "El agente no logró al menos 2 fuentes oficiales verificables para "
            f"'{datos.get('tema','')}'. No se generó contenido por seguridad."
        )
        enviar(asunto + " (sin tema)", aviso, f"<p>{escape(aviso)}</p>", dry)
        return

    texto, html = construir_correo(categoria, datos, fuentes)
    enviar(asunto, texto, html, dry)
    if not dry:
        guardar_historial(hist, categoria, datos["tema"], [f["url"] for f in fuentes])


if __name__ == "__main__":
    main()
