"""Draws neutral studio-style T-shirt placeholders with Pillow. Replace with real photography."""
import io
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H, SS = 900, 1125, 2  # output size, supersampling factor

TEE = [  # normalised to a 1000-unit box
    (330, 170), (400, 150), (600, 150), (670, 170), (850, 260), (930, 440), (785, 495),
    (730, 410), (730, 905), (270, 905), (270, 410), (215, 495), (70, 440), (150, 260),
]


def _hex(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _shade(rgb, factor):
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


def _background():
    top, bottom = (238, 238, 235), (222, 222, 218)
    gradient = Image.linear_gradient("L").resize((W * SS, H * SS))
    return Image.composite(Image.new("RGB", gradient.size, bottom), Image.new("RGB", gradient.size, top), gradient)


def _tee_layer(color, scale=0.95):
    base = _hex(color)
    size = (W * SS, H * SS)
    s = scale * W * SS / 1000
    ox, oy = (W * SS - 1000 * s) / 2, H * SS * 0.07

    def pt(p):
        return (ox + p[0] * s, oy + p[1] * s)

    # soft contact shadow
    shadow = Image.new("L", size, 0)
    ImageDraw.Draw(shadow).ellipse([pt((230, 880)), pt((770, 960))], fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(28 * SS))

    shirt = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shirt)
    d.polygon([pt(p) for p in TEE], fill=base + (255,))
    # sleeve seams + side shading for volume
    seam = _shade(base, 0.88) + (255,)
    d.line([pt((330, 170)), pt((215, 495))], fill=seam, width=int(3 * SS))
    d.line([pt((670, 170)), pt((785, 495))], fill=seam, width=int(3 * SS))
    for x0, x1 in ((270, 340), (660, 730)):
        shade = Image.new("L", size, 0)
        ImageDraw.Draw(shade).rectangle([pt((x0, 420)), pt((x1, 905))], fill=40)
        shade = shade.filter(ImageFilter.GaussianBlur(30 * SS))
        shirt.paste(Image.new("RGBA", size, _shade(base, 0.7) + (255,)), mask=shade)
    # hem line
    d.line([pt((272, 880)), pt((728, 880))], fill=seam, width=int(3 * SS))
    # collar: inner back neck, then rib
    d.ellipse([pt((395, 105)), pt((605, 265))], fill=_shade(base, 0.62) + (255,))
    d.arc([pt((385, 95)), pt((615, 285))], 0, 180, fill=_shade(base, 0.82) + (255,), width=int(16 * SS))
    # clip the collar's upper half so it reads as a neckline cut
    d.rectangle([pt((380, 60)), pt((620, 150))], fill=(0, 0, 0, 0))

    # fabric grain
    grain = Image.effect_noise(size, 18).convert("L")
    mask = shirt.getchannel("A")
    textured = ImageChops.add(shirt.convert("RGB"), Image.merge("RGB", (grain, grain, grain)), scale=1, offset=-128)
    shirt = Image.merge("RGBA", (*textured.split(), mask))
    return shadow, shirt


def render_front(color):
    canvas = _background()
    shadow, shirt = _tee_layer(color)
    canvas.paste(Image.new("RGB", canvas.size, (120, 120, 116)), mask=shadow)
    canvas.paste(shirt, mask=shirt.getchannel("A"))
    return canvas.resize((W, H), Image.LANCZOS)


def render_detail(color):
    """Close crop on the collar and shoulder, for the second gallery image."""
    full = render_front(color).resize((W * SS, H * SS), Image.LANCZOS)
    box = (int(W * SS * 0.22), int(H * SS * 0.12), int(W * SS * 0.78), int(H * SS * 0.12 + W * SS * 0.56 * 1.25))
    return full.crop(box).resize((W, H), Image.LANCZOS)


def to_jpeg_bytes(image, quality=84):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def category_image(seed, color):
    random.seed(seed)
    return render_front(color)
