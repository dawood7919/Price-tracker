from utils.helpers import format_price

WELCOME_MESSAGE = (
    "👋 أهلاً بيك في بوت تتبع الأسعار!\n\n"
    "ابعتلي رابط أي منتج من Amazon أو Noon وأنا هتابعلك سعره وأبلغك أول ما ينزل.\n\n"
    "الأوامر المتاحة:\n"
    "/add <رابط المنتج> - إضافة منتج للمتابعة\n"
    "/list - عرض المنتجات اللي بتتابعها\n"
    "/remove <رقم المنتج> - إيقاف متابعة منتج\n"
    "/help - عرض المساعدة"
)

HELP_MESSAGE = WELCOME_MESSAGE


def product_added_message(product: dict) -> str:
    return (
        "✅ تم إضافة المنتج بنجاح\n\n"
        f"المنتج:\n{product['name']}\n\n"
        f"السعر الحالي:\n{format_price(product['price'], product['currency'])}\n\n"
        "هفحص السعر كل ساعة."
    )


def price_drop_message(product_name: str, previous_price: float, current_price: float, currency: str, url: str) -> str:
    discount = previous_price - current_price
    return (
        "🔥 نزل سعر منتج بتتابعه!\n\n"
        f"المنتج:\n{product_name}\n\n"
        f"السعر السابق:\n{format_price(previous_price, currency)}\n\n"
        f"السعر الحالي:\n{format_price(current_price, currency)}\n\n"
        f"وفرت:\n{format_price(discount, currency)}\n\n"
        f"الرابط:\n{url}"
    )


def product_list_message(products) -> str:
    if not products:
        return "مفيش منتجات بتتابعها دلوقتي. ابعت /add <رابط> عشان تبدأ."

    lines = ["📋 المنتجات اللي بتتابعها:\n"]
    for p in products:
        lines.append(
            f"#{p['id']} - {p['product_name']}\n"
            f"السعر الحالي: {format_price(p['current_price'])}\n"
            f"المتجر: {p['store_name']}\n"
        )
    return "\n".join(lines)


def usage_add_message() -> str:
    return "لازم تبعت رابط المنتج بعد الأمر، مثال:\n/add https://www.amazon.ae/dp/xxxxx"


def usage_remove_message() -> str:
    return "لازم تبعت رقم المنتج بعد الأمر، مثال:\n/remove 3"


def product_not_found_message() -> str:
    return "مفيش منتج بهذا الرقم في قائمتك."


def product_removed_message(name: str) -> str:
    return f"🗑️ تم إيقاف متابعة: {name}"


def scrape_error_message(error: str) -> str:
    return f"⚠️ مقدرتش أجيب بيانات المنتج من الرابط ده.\nالسبب: {error}"


def unsupported_site_message() -> str:
    return "⚠️ الموقع ده مش مدعوم لسه. المدعوم حاليًا: Amazon و Noon."
