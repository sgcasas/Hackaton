"""Pone los correos (y celulares) del grupo a un rango de clientes de la semilla, para la demo.
SES en sandbox solo envía a correos verificados (README §10).

Uso:
  python scripts/correos_demo.py --correos a@utec.edu.pe,b@utec.edu.pe --desde 1 --hasta 30
  python scripts/correos_demo.py --correos ... --celular +51987654321   (opcional, para SMS)
"""
import argparse

import boto3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--correos", required=True)
    ap.add_argument("--celular", default="")
    ap.add_argument("--desde", type=int, default=1)
    ap.add_argument("--hasta", type=int, default=30)
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    correos = [c.strip() for c in a.correos.split(",") if c.strip()]
    t = boto3.resource("dynamodb", region_name=a.region).Table("redtelecorp-clientes")
    for n, i in enumerate(range(a.desde, a.hasta + 1)):
        expr, vals = "SET email = :e", {":e": correos[n % len(correos)]}
        if a.celular:
            expr += ", celular = :c"
            vals[":c"] = a.celular
        t.update_item(Key={"pk": f"DNI#{70000000 + i:08d}", "sk": f"NUM#9{87000000 + i:08d}"},
                      UpdateExpression=expr, ExpressionAttributeValues=vals)
    print(f"Clientes {a.desde}..{a.hasta} ahora usan {', '.join(correos)}")


if __name__ == "__main__":
    main()
