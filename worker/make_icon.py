"""Genera un .ico de marca ENTOMIRA (emblema: alas verdes + lente ámbar)."""
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

VERDE_OSC = (27, 67, 50)
VERDE_CLR = (82, 183, 136)
AMBAR = (247, 169, 59)
AMBAR_OSC = (232, 135, 27)
FONDO = (15, 30, 23)

OUT = Path(__file__).parent / "entomira.ico"


def draw(size: int) -> Image.Image:
    s = size * 4  # supersampling
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = s // 2

    # Disco de fondo redondeado
    d.ellipse([s*0.06, s*0.06, s*0.94, s*0.94], fill=FONDO)

    # Alas (dos gotas verdes simétricas)
    for sign in (-1, 1):
        d.polygon([
            (cx, cy),
            (cx + sign * s*0.22, cy - s*0.10),
            (cx + sign * s*0.14, cy - s*0.34),
            (cx + sign * s*0.02, cy - s*0.12),
        ], fill=VERDE_OSC)
        d.polygon([
            (cx, cy),
            (cx + sign * s*0.18, cy - s*0.06),
            (cx + sign * s*0.10, cy - s*0.26),
            (cx + sign * s*0.02, cy - s*0.10),
        ], fill=VERDE_CLR)

    # Antenas
    for sign in (-1, 1):
        d.line([(cx, cy), (cx + sign * s*0.10, cy - s*0.30)],
               fill=VERDE_CLR, width=max(2, s // 120))

    # Lente / sensor ámbar con halo
    r = s * 0.13
    d.ellipse([cx - r*1.5, cy - r*1.5, cx + r*1.5, cy + r*1.5],
              outline=AMBAR, width=max(2, s // 160))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=AMBAR)
    d.ellipse([cx - r*0.55, cy - r*0.7, cx + r*0.2, cy], fill=(255, 222, 138))

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    # De MAYOR a menor, y el grande como base.
    #
    # Antes la base era el 16x16 y Pillow no puede AMPLIAR: descartaba en silencio todos los
    # tamanos mayores y el .ico acababa teniendo solo 16x16. Windows lo estiraba a 48 y a 256
    # en el escritorio, y por eso los accesos directos se veian borrosos. Cada tamano se dibuja
    # aparte (con supersampling), que es lo que hace que el de 16 px siga siendo legible.
    sizes = [256, 128, 64, 48, 32, 24, 16]
    imgs = [draw(sz) for sz in sizes]
    imgs[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes],
                 append_images=imgs[1:])

    from PIL import Image as _I
    with _I.open(OUT) as ver:
        incluidos = sorted({t[0] for t in ver.info.get("sizes", [])})
    print(f"Icono generado: {OUT}")
    print(f"  tamanos dentro del .ico: {incluidos}")
    if len(incluidos) < len(sizes):
        raise SystemExit("El .ico no lleva todos los tamanos: se veria borroso al ampliarlo.")
    refrescar_cache_de_iconos()


def refrescar_cache_de_iconos() -> None:
    r"""Dile a Windows que vuelva a leer el icono. SIN ESTO, arreglar el .ico NO SE VE.

    Windows guarda los iconos ya pintados en `%LocalAppData%\Microsoft\Windows\Explorer\
    iconcache_*.db` y los sirve de ahi aunque el fichero de origen haya cambiado. El 2026-09-12
    se arreglo el .ico (llevaba un solo mapa de 16x16) y los accesos directos **siguieron
    viendose borrosos**: lo que el escritorio pintaba era la copia vieja de la cache, de la
    madrugada anterior.

    Es el mismo modo de fallo de siempre en este proyecto: nada dio error, el fichero estaba
    bien, y lo unico que decia la verdad era mirar la pantalla.

    `ie4uinit` es la via documentada de Windows y **no cierra ninguna ventana**; a diferencia de
    matar el explorador, que es lo que hay que hacer si esto no basta.
    """
    if os.name != "nt":
        return
    for args in (["ie4uinit.exe", "-show"], ["ie4uinit.exe", "-ClearIconCache"]):
        try:
            subprocess.run(args, check=False, timeout=20,
                           creationflags=0x08000000)          # CREATE_NO_WINDOW
        except Exception:                                     # noqa: BLE001
            pass
    try:                                                      # avisa al shell del cambio
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # ASSOCCHANGED
    except Exception:                                         # noqa: BLE001
        pass
    print("  cache de iconos de Windows refrescada")
    print("  si AUN se ve borroso: cierra sesion y vuelve a entrar, o reinicia el explorador.")


if __name__ == "__main__":
    main()
