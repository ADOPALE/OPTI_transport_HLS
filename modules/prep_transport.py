"""
prep_transport.py — Préparation des flux du jour pour le moteur transport
=========================================================================

Construit le DataFrame `df_jour` harmonisé attendu par
`moteur_postes.optimiser_postes_jour`, à partir de l'onglet « M flux ».

On ne retient que les flux de NATURE « Volume » (les flux « Fréquences » sont
des obligations de passage gérées ailleurs), dont la quantité du jour > 0.

Colonnes produites (en plus de celles de M flux) :
  - 'Quantite_du_jour'        : quantité de contenants pour le jour demandé
  - 'Type (propre/sale)'      : recopie de 'Sale / propre'
Les colonnes d'origine conservées et utilisées par le moteur :
  'Point de départ', 'Point de destination', 'Nature de contenant',
  'Fonction Support associée', 'Heure de mise à disposition min départ',
  'Heure max de livraison à la destination'.
"""

import pandas as pd

_JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def _col(df, *cles):
    """Trouve une colonne par tolérance (casse, espaces, fragments)."""
    norm = {str(c).strip().lower(): c for c in df.columns}
    for cle in cles:
        k = cle.strip().lower()
        if k in norm:
            return norm[k]
    for cle in cles:
        k = cle.strip().lower()
        for c in df.columns:
            if k in str(c).strip().lower():
                return c
    return None


def preparer_flux_complets_du_jour(df_flux, jour, nature_volume="Volume"):
    """
    Renvoie le DataFrame des flux actifs (Volume, quantité > 0) pour `jour`.

    df_flux : onglet « M flux » brut.
    jour    : 'Lundi'..'Dimanche'.
    """
    if jour not in _JOURS:
        raise ValueError(f"Jour invalide : {jour!r} (attendu : {_JOURS})")

    df = df_flux.copy()
    # colonne « nature du flux » = celle qui distingue Volume / Fréquences
    # (et NON le champ libre). On la repère par son libellé long, sinon index 12.
    col_nature = _col(df, "obligation de passage", "les tournées sont elles")
    if col_nature is None:
        col_nature = df.columns[12] if len(df.columns) > 12 else _col(df, "Nature du flux")
    col_qte = _col(df, f"Quantité {jour}", f"Quantite {jour}")
    if col_qte is None:
        raise KeyError(f"Colonne 'Quantité {jour}' introuvable dans M flux.")

    # filtre nature Volume
    masque_vol = df[col_nature].astype(str).str.strip().str.lower().str.startswith(
        str(nature_volume).strip().lower())
    df = df[masque_vol].copy()

    # quantité du jour
    df["Quantite_du_jour"] = pd.to_numeric(df[col_qte], errors="coerce").fillna(0)
    df = df[df["Quantite_du_jour"] > 0].copy()

    # harmonisation des colonnes attendues
    col_ps = _col(df, "Sale / propre", "Sale/propre", "propre / sale")
    df["Type (propre/sale)"] = (df[col_ps].astype(str).str.strip()
                                if col_ps is not None else "Propre")

    # garantir la présence des colonnes clés (renommage tolérant)
    renommage = {
        _col(df, "Point de départ", "Point de depart"): "Point de départ",
        _col(df, "Point de destination"): "Point de destination",
        _col(df, "Nature de contenant"): "Nature de contenant",
        _col(df, "Fonction Support associée", "Fonction Support"): "Fonction Support associée",
        _col(df, "Heure de mise à disposition min départ",
             "Heure de mise a disposition"): "Heure de mise à disposition min départ",
        _col(df, "Heure max de livraison à la destination",
             "Heure max de livraison"): "Heure max de livraison à la destination",
    }
    renommage = {k: v for k, v in renommage.items() if k is not None and k != v}
    df = df.rename(columns=renommage)
    df = df.reset_index(drop=True)
    return df
