"""Small Tk layout helpers shared by desktop pages."""
import tkinter as tk
from tkinter import ttk


def scroll_page(parent):
    page = ttk.Frame(parent)
    canvas = tk.Canvas(page, bg="#101720", highlightthickness=0)
    scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    content = ttk.Frame(canvas)
    item = canvas.create_window(0, 0, anchor="nw", window=content)
    content.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
    return page, content
