"""
Script de diagnóstico: compara carácter por carácter dos strings
para detectar diferencias invisibles (espacios, guiones especiales, etc.)
"""

def diagnosticar(numero_proceso_sercop, valor_gemini):
    print(f"Valor Supabase: {repr(valor_supabase)}  (longitud: {len(valor_supabase)})")
    print(f"Valor Gemini:   {repr(valor_gemini)}  (longitud: {len(valor_gemini)})")
    print()

    if valor_supabase == valor_gemini:
        print("✅ Son IDÉNTICOS carácter por carácter.")
        return

    print("❌ NO son idénticos. Diferencias encontradas:")
    max_len = max(len(valor_supabase), len(valor_gemini))
    for i in range(max_len):
        c1 = valor_supabase[i] if i < len(valor_supabase) else "(falta)"
        c2 = valor_gemini[i] if i < len(valor_gemini) else "(falta)"
        if c1 != c2:
            cod1 = hex(ord(c1)) if c1 != "(falta)" else "-"
            cod2 = hex(ord(c2)) if c2 != "(falta)" else "-"
            print(f"  Posición {i}: Supabase='{c1}' ({cod1})  vs  Gemini='{c2}' ({cod2})")


if __name__ == "__main__":
    # PEGA AQUÍ el valor exacto copiado de Supabase (Table Editor -> click en la celda -> copiar)
    valor_supabase = "CATE-GADPSDT-2023-001"

    # PEGA AQUÍ el valor que imprime Gemini (ver instrucción abajo para obtenerlo)
    valor_gemini = "CATE-GADPSDT-2023-001"

    diagnosticar(valor_supabase, valor_gemini)