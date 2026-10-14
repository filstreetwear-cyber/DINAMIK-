"""
HTML sintetico per i test Anello 1, costruito sui selettori delle pagine
Amazon EU che page_parser.py usa. NON è HTML reale di Amazon: quando il
primo run dal PC di casa salva pagine vere in data/evidence/, le più
rappresentative vanno copiate qui accanto come fixture reali.
"""
from __future__ import annotations


def product_html(
    *,
    title: str = "Organizer cassetti cucina in bambù, set da 6 contenitori",
    brand_byline: str = "Visita lo Store di CasaBella",
    seller_name: str | None = "CasaBella Srl",
    seller_id: str | None = "A1SELLERIT0001",
    seller_mode: str = "trigger",  # trigger | tabular | amazon | none
    rating: str = "4,1 su 5 stelle",
    reviews: str = "37 voti",
    price: str = "24,99€",
    bsr_line: str = "Posizione nella classifica Bestseller di Amazon: n. 1.234 in Casa e cucina (Visualizza i Top 100)",
    images: int = 3,
    video: bool = False,
    a_plus: bool = False,
    bullets: int = 3,
) -> str:
    if seller_mode == "trigger":
        seller_block = (
            f'<div id="merchant-info">Venduto da <a id="sellerProfileTriggerId" '
            f'href="/gp/help/seller/at-a-glance.html/ref=dp_merchant_link?ie=UTF8&seller={seller_id}&isAmazonFulfilled=1">'
            f"{seller_name}</a></div>"
        )
    elif seller_mode == "tabular":
        seller_block = (
            '<div id="tabular-buybox"><div class="tabular-buybox-text" tabular-attribute-name="Venduto da">'
            f'<span><a href="/gp/aag/main?seller={seller_id}">{seller_name}</a></span></div></div>'
        )
    elif seller_mode == "amazon":
        seller_block = '<div id="merchant-info">Spedito e venduto da Amazon.</div>'
    else:
        seller_block = ""
    thumbs = "".join(
        f'<li class="a-spacing-small item imageThumbnail"><img src="/images/I/img{i}.jpg"></li>'
        for i in range(images)
    )
    video_li = '<li class="videoThumbnail"><img src="v.jpg"></li>' if video else ""
    bullet_items = "".join(f'<li><span class="a-list-item">Punto {i}</span></li>' for i in range(bullets))
    aplus = '<div id="aplus"><div class="aplus-v2">A+</div></div>' if a_plus else ""
    return f"""<html><body>
<span id="productTitle"> {title} </span>
<a id="bylineInfo" href="/stores/CasaBella">{brand_byline}</a>
<span id="acrPopover" title="{rating}"></span>
<span id="acrCustomerReviewText">{reviews}</span>
<span class="a-price"><span class="a-offscreen">{price}</span></span>
{seller_block}
<div id="altImages"><ul>{thumbs}{video_li}</ul></div>
<div id="feature-bullets"><ul>{bullet_items}</ul></div>
<div id="detailBulletsWrapper_feature_div"><ul><li><span>{bsr_line}</span></li></ul></div>
{aplus}
</body></html>"""


def seller_html(
    *,
    display_name: str = "CasaBella Srl",
    business_name: str | None = "CASABELLA S.R.L.",
    vat: str | None = "IT01234567890",
    address_lines: tuple[str, ...] = ("Via Roma 1", "Milano", "MI", "20121", "IT"),
) -> str:
    rows = []
    if business_name:
        rows.append(f'<div class="a-row"><span>Nome dell\'azienda:</span><span>{business_name}</span></div>')
    if vat:
        rows.append(f'<div class="a-row"><span>Partita IVA:</span><span>{vat}</span></div>')
    if address_lines:
        rows.append('<div class="a-row"><span>Indirizzo aziendale:</span></div>')
        rows += [f'<div class="a-row indent-left"><span>{line}</span></div>' for line in address_lines]
    return f"""<html><body>
<h1 id="seller-name">{display_name}</h1>
<div id="page-section-detail-seller-info"><div class="a-box-inner">
<h3>Informazioni dettagliate sul venditore</h3>
<div class="a-row a-spacing-none">{"".join(rows)}</div>
</div></div>
</body></html>"""


def listing_html(asins: list[str]) -> str:
    cards = "".join(
        f'<div class="zg-grid-general-faceout" data-asin="{a}"><a href="/Prodotto-X/dp/{a}/ref=zg_bs">x</a></div>'
        for a in asins
    )
    return f"<html><body><div id='gridItemRoot'>{cards}</div></body></html>"


CAPTCHA_HTML = """<html><body><form action="/errors/validateCaptcha">
<h4>Inserisci i caratteri che vedi qui sotto</h4><input id="captchacharacters"></form></body></html>"""
