"""Pares de pregunta/respuesta de la FAQ (C-5, DT-7).

Fuente única de la FAQ de demo: `scripts/seed_faq.py` los baja a `faq_entries` con sus embeddings.
Son respuestas generales del negocio inmobiliario en Guadalajara, no de una propiedad concreta: para
una propiedad específica el agente usa las tools del catálogo. Sin categorías protegidas ni
direcciones exactas (guardrails.md), y sin prometer montos o plazos que dependan del caso.

Abrir a 15-25 pares es el criterio de aceptación de la historia; si se agregan o quitan, se vuelve a
correr el seed (idempotente por pregunta).
"""
from __future__ import annotations

FAQ: list[dict[str, str]] = [
    {"question": "¿Cuánto necesito de enganche para comprar una casa o departamento?",
     "answer": "Depende del tipo de operación y del crédito. En venta y preventa se pide normalmente "
               "entre el 10% y el 20% del precio como enganche; el resto se cubre con crédito "
               "hipotecario o pago directo. Con tu perfil exacto te decimos el monto para una "
               "propiedad concreta."},
    {"question": "¿Qué requisitos piden para rentar?",
     "answer": "Para renta suelen pedirse identificación oficial, comprobantes de ingresos y "
               "referencias. Según el caso puede solicitarse un aval o un depósito en garantía. Los "
               "requisitos exactos varían por propiedad."},
    {"question": "¿Puedo comprar con crédito Infonavit o Fovissste?",
     "answer": "Sí, trabajamos con créditos Infonavit, Fovissste y bancarios. Validamos tu "
               "precalificación y te indicamos qué propiedades aplican a tu esquema de crédito."},
    {"question": "¿Cuánto cobran de comisión?",
     "answer": "La comisión se cobra al cerrar la operación y se acuerda por escrito antes de "
               "firmar. En venta la cubre habitualmente el vendedor; en renta se define según el "
               "contrato. Te compartimos el monto exacto antes de cualquier compromiso."},
    {"question": "¿En cuánto tiempo se firma la escritura?",
     "answer": "Después de aceptar la oferta y aprobar el crédito, la firma ante notario suele "
               "tomar de tres a seis semanas. Si el pago es directo puede ser más rápido. El plazo "
               "depende de la notaría y del banco."},
    {"question": "¿Hacen avalúo de la propiedad?",
     "answer": "Sí. Si usas crédito hipotecario, el banco pide un avalúo por un perito autorizado. "
               "También podemos coordinar un avalúo de referencia para compra directa."},
    {"question": "¿Puedo apartar una propiedad con un anticipo?",
     "answer": "Sí, la propiedad se puede apartar con un anticipo y se firma un convenio con las "
               "condiciones y plazos. Mientras el apartado esté vigente, la propiedad no se ofrece a "
               "otra persona."},
    {"question": "¿Cómo agendo una visita a una propiedad?",
     "answer": "Dime qué propiedad te interesa y tu disponibilidad, y te propongo horarios con un "
               "asesor. La visita se confirma con fecha, hora y asesor asignado, y recibes un "
               "recordatorio el día anterior."},
    {"question": "¿Atienden visitas los fines de semana?",
     "answer": "Sí, hay asesores disponibles sábados y domingos con cita previa. Entre semana también "
               "se puede agendar en horario extendido según la agenda del asesor."},
    {"question": "¿Tienen propiedades en preventa?",
     "answer": "Sí, manejamos desarrollos en preventa. En preventa el precio de lanzamiento suele ser "
               "menor y el pago se hace por etapas durante la construcción. Te mostramos las opciones "
               "disponibles."},
    {"question": "¿Qué zonas de Guadalajara manejan?",
     "answer": "Tenemos inventario en zonas como Providencia, Zapopan, Chapalita, Andares y "
               "Tlaquepaque, entre otras. Dime en qué zona te interesa y te muestro lo disponible ahí."},
    {"question": "¿Aceptan mascotas en las rentas?",
     "answer": "Depende del reglamento de cada propiedad y del acuerdo con el propietario. Algunas "
               "rentas aceptan mascotas con depósito adicional. Dime de qué propiedad se trata y lo "
               "confirmamos."},
    {"question": "¿El precio incluye estacionamiento y amenidades?",
     "answer": "No todas las propiedades incluyen estacionamiento o amenidades en el precio. Cada "
               "ficha trae sus amenidades y, si aplica, el número de estacionamientos. Te lo "
               "confirmamos por propiedad."},
    {"question": "¿Qué gastos de cierre hay además del precio?",
     "answer": "Además del precio están los gastos de escrituración, notaría, avalúo y, si hay "
               "crédito, la comisión por apertura. Son un porcentaje del valor y te los estimamos "
               "antes de firmar."},
    {"question": "¿Ofrecen plan de pagos o financiamiento directo?",
     "answer": "Algunos desarrollos ofrecen plan de pagos directo con el desarrollador, sobre todo en "
               "preventa. Revisamos qué propiedades tienen ese esquema y las condiciones de "
               "mensualidades."},
    {"question": "¿Puedo vender mi propiedad actual y usar ese dinero para comprar otra?",
     "answer": "Sí. Podemos ayudarte a valuar y vender tu propiedad actual y coordinar los tiempos "
               "con la compra de la nueva, incluso si necesitas apartar antes de cerrar la venta."},
    {"question": "¿Cuánto tarda la aprobación de un crédito hipotecario?",
     "answer": "La precalificación puede salir en pocos días y la aprobación formal entre una y tres "
               "semanas, según el banco y la documentación. Con tu precalificación lista, avanzo más "
               "rápido con la propiedad que elijas."},
    {"question": "¿Las propiedades están libres de gravamen y con escrituras?",
     "answer": "Sí. Antes de cerrar verificamos la situación legal de la propiedad, que esté libre de "
               "gravamen y con escrituras en orden. La notaría hace la revisión final en la "
               "escrituración."},
    {"question": "¿Puedo cambiar de propiedad si ya aparté otra?",
     "answer": "Se puede revisar según las condiciones del convenio de apartado. En general el "
               "anticipo es transferible a otra propiedad del mismo desarrollador dentro de la "
               "vigencia. Te confirmamos las condiciones de tu caso."},
    {"question": "¿Cómo cuidan mis datos personales?",
     "answer": "Tus datos se usan solo para atender tu búsqueda y coordinar visitas, y puedes pedir "
               "que ya no te contactemos en cualquier momento. No compartimos tu información con "
               "terceros sin tu consentimiento."},
]
