import streamlit as st
import pandas as pd
import numpy as np
from Import import extraction_donnees

def segmenter_flux(df):
    """
    Sépare les flux 'Volume' en Récurrents (Lundi au Vendredi) et Spécifiques.
    """
    # 1. Nom exact de la colonne de nature du flux
    col_nature = "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)"
    
    # Filtrage initial sur le type "Volume"
    df_volume = df[df[col_nature] == "Volume"].copy()
    
    # 2. Définition des colonnes de la semaine vs Week-end
    jours_semaine = [
        "Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", 
        "Quantité Jeudi", "Quantité Vendredi"
    ]
    jours_weekend = ["Quantité Samedi", "Quantité Dimanche"]
    tous_jours = jours_semaine + jours_weekend
    
    # Sécurité : on remplace les cases vides par 0
    df_volume[tous_jours] = df_volume[tous_jours].fillna(0)
    
    # 3. Logique de segmentation NUANCÉE
    # Un flux est récurrent s'il y a du volume TOUS les jours de la semaine (L-V)
    masque_recurrent = (df_volume[jours_semaine] > 0).all(axis=1)
    
    df_recurrent = df_volume[masque_recurrent].copy()
    df_specifique = df_volume[~masque_recurrent].copy()
    
    return df_recurrent, df_specifique
