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

# --- Exécution dans l'interface Streamlit ---

st.title("🚚 Simulation des Flux Lourds")

if "data" not in st.session_state:
    st.warning("⚠️ Veuillez charger les données dans l'onglet Importation avant de continuer.")
else:
    # On récupère le DataFrame m_flux extrait dans le premier fichier
    m_flux = st.session_state["data"]["m_flux"]
    
    # Appel de la fonction de segmentation
    flux_rec, flux_spec = segmenter_flux(m_flux)
    
    # Affichage des résultats
    st.subheader("Résultats de la segmentation")
    
    c1, c2 = st.columns(2)
    c1.metric("Flux Récurrents (7j/7)", len(flux_rec))
    c2.metric("Flux Spécifiques", len(flux_spec))
    
    with st.expander("Consulter les flux récurrents"):
        st.dataframe(flux_rec)
        
    with st.expander("Consulter les flux spécifiques"):
        st.dataframe(flux_spec)
