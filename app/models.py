from pydantic import BaseModel


class Intent(BaseModel):
    request: str
    max_pence: int
    expires_at: str
    nonce: str


class Fact(BaseModel):
    subject: str
    relation: str
    object: str
    signed_at: str
    key_id: str


class CartItem(BaseModel):
    name: str
    colour: str
    waist: int
    material: str
    price_pence: int
    description: str = ""
    kind: str = "jeans"
    image: str = ""


class Undo(BaseModel):
    window_hours: int
    cancel_url: str | None
    fee_pence: int


class CartOffer(BaseModel):
    merchant_id: str
    items: list[CartItem]
    undo: Undo
    intent_hash: str
    expires_at: str
