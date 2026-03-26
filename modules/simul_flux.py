import pandas as pd
import streamlit as st

def convertir_temps_excel(valeur):
    """Convertit un format 00:10:00 ou une chaîne en minutes (float)."""
    if hasattr(valeur, 'hour'): # Cas du format Heure Excel
        return valeur.hour * 60 + valeur.minute + valeur.second / 60
    try:
        # Cas d'une chaîne "00:10:00"
        parts = str(valeur).split(':')
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
        return float(valeur)
    except:
        return 0.0

def calculer_duree_rotation(mission, vehicule_name, qte_a_transporter, df_vehicules, matrice_duree):
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # Conversion forcée des temps de manutention
    t_quai = convertir_temps_excel(spec_v['Temps de mise à quai - manœuvre, contact/admin min (minutes)'])
    t_unit = convertir_temps_excel(spec_v['Manutention on sans quai (minutes / contenants)'])
    
    # Calcul : (Quai A + Quai B) + (Manutention unitaire * Quantité * 2 pour charger/décharger)
    manutention_totale = (t_quai * 2) + (t_unit * qte_a_transporter * 2)
    
    try:
        duree_trajet = matrice_duree.loc[mission['origine'], mission['destination']] * 2
    except:
        duree_trajet = 60 # Valeur de secours si trajet inconnu
    
    return manutention_totale + duree_trajet

def preparer_missions_unifiees(df_flux):
    # Nettoyage des colonnes pour éviter les espaces invisibles
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    # Recherche flexible de la colonne "Nature"
    col_nature = [c for c in df_flux.columns if "Nature du flux" in c][0]
    
    # On accepte "Volume", "Volumes", "VOLUME" etc.
    df_vol = df_flux[df_flux[col_nature].astype(str).str.contains("Volume", case=False, na=False)].copy()
    
    # ... (restez sur votre logique de boucle actuelle pour créer les missions)
