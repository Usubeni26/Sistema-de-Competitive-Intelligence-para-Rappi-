import json
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import ttk, messagebox

# ==============================
# CONFIG
# ==============================
DATA_DIR = Path("results/JSON")

# ==============================
# LOAD DATA
# ==============================
def load_all_data():
    all_files = list(DATA_DIR.glob("results_*.json"))
    dfs = []

    for file in all_files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            df = pd.DataFrame(data)
            dfs.append(df)
        except Exception as e:
            print(f"Error leyendo {file}: {e}")

    if dfs:
        return pd.concat(dfs, ignore_index=True)
    return pd.DataFrame()

# ==============================
# APP
# ==============================
class App:
    def __init__(self, root, df):
        self.root = root
        self.df = df

        self.root.title("Comparador de Plataformas")
        self.root.geometry("1000x700")
        self.root.configure(bg="#f5f5f5")

        # Estilo
        style = ttk.Style()
        style.configure("TLabel", font=("Arial", 11))
        style.configure("TButton", font=("Arial", 11, "bold"))
        style.configure("TCheckbutton", font=("Arial", 10))

        # ==============================
        # CONTENEDORES
        # ==============================
        main_frame = ttk.Frame(root, padding=15)
        main_frame.pack(fill="both", expand=True)

        left_frame = ttk.LabelFrame(main_frame, text="Configuración", padding=10)
        left_frame.pack(side="left", fill="y", padx=10, pady=10)

        right_frame = ttk.LabelFrame(main_frame, text="Acciones", padding=10)
        right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        # ==============================
        # PLATAFORMAS
        # ==============================
        ttk.Label(left_frame, text="Plataformas (Eje X):").pack(anchor="w", pady=5)

        self.platform_vars = {}
        platforms = sorted(df["empresa"].dropna().unique())

        for p in platforms:
            var = tk.BooleanVar(value=True)
            self.platform_vars[p] = var
            ttk.Checkbutton(left_frame, text=p, variable=var).pack(anchor="w")

        # ==============================
        # ORDEN
        # ==============================
        ttk.Label(left_frame, text="Orden a comparar:").pack(anchor="w", pady=10)

        self.order_var = tk.StringVar()
        orders = sorted(df["order"].dropna().unique())
        self.order_combo = ttk.Combobox(left_frame, textvariable=self.order_var, values=orders, state="readonly")
        self.order_combo.pack(fill="x")

        # ==============================
        # UBICACIÓN
        # ==============================
        ttk.Label(left_frame, text="Ubicación:").pack(anchor="w", pady=10)

        self.use_location = tk.BooleanVar(value=False)
        ttk.Checkbutton(left_frame, text="Filtrar por ubicación", variable=self.use_location).pack(anchor="w")

        self.location_var = tk.StringVar()
        locations = sorted(df["address_id"].dropna().unique()) if "address_id" in df else []
        self.location_combo = ttk.Combobox(left_frame, textvariable=self.location_var, values=locations, state="readonly")
        self.location_combo.pack(fill="x")

        # ==============================
        # MÉTRICAS
        # ==============================
        ttk.Label(left_frame, text="Métricas (Eje Y):").pack(anchor="w", pady=10)

        self.metrics = {
            "tiempo_envio": tk.BooleanVar(value=True),
            "costo_envio": tk.BooleanVar(value=False),
            "costo_total": tk.BooleanVar(value=True),
            "costo_retail": tk.BooleanVar(value=False),
        }

        for m, var in self.metrics.items():
            ttk.Checkbutton(left_frame, text=m, variable=var).pack(anchor="w")

        # ==============================
        # BOTÓN
        # ==============================
        ttk.Button(right_frame, text="Generar gráficas", command=self.plot).pack(pady=20)

        self.status_label = ttk.Label(right_frame, text="Selecciona parámetros y genera la gráfica", foreground="gray")
        self.status_label.pack()

    def clean_money(self, val):
        try:
            return float(str(val).replace("$", ""))
        except:
            return None

    def plot(self):
        selected_platforms = [p for p, v in self.platform_vars.items() if v.get()]
        selected_order = self.order_var.get()
        selected_metrics = [m for m, v in self.metrics.items() if v.get()]

        # 🚨 Validación de orden
        if not selected_order:
            messagebox.showerror("Error", "Debes seleccionar una orden para graficar.")
            return

        df = self.df.copy()

        df = df[df["empresa"].isin(selected_platforms)]
        df = df[df["order"] == selected_order]

        if self.use_location.get() and self.location_var.get():
            df = df[df["address_id"] == self.location_var.get()]

        if df.empty:
            messagebox.showwarning("Sin datos", "No hay datos para graficar con los filtros seleccionados.")
            return

        for col in ["costo_envio", "costo_total", "costo_retail"]:
            if col in df:
                df[col] = df[col].apply(self.clean_money)

        color_map = {
            "RAPPI": "orange",
            "DIDI": "yellow",
            "UBEREATS": "green"
        }

        for metric in selected_metrics:
            if metric not in df:
                continue

            values = []
            labels = []
            colors = []

            for platform in selected_platforms:
                subset = df[df["empresa"] == platform]

                if subset.empty:
                    values.append(0)
                else:
                    val = subset[metric].mean()
                    values.append(val if val is not None else 0)

                labels.append(platform)
                colors.append(color_map.get(platform, "gray"))

            plt.figure()
            plt.bar(labels, values, color=colors)

            title = f"{metric} - {selected_order}"
            if self.use_location.get() and self.location_var.get():
                title += f" ({self.location_var.get()})"

            plt.title(title)
            plt.xlabel("Plataformas")
            plt.ylabel(metric)
            plt.tight_layout()
            plt.show(block=False)

        self.status_label.config(text="Gráficas generadas correctamente", foreground="green")

# ==============================
# MAIN
# ==============================
if __name__ == "__main__":
    df = load_all_data()

    if df.empty:
        print("No hay datos")
        exit()

    root = tk.Tk()
    app = App(root, df)
    root.mainloop()
