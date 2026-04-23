import streamlit as st
import pandas as pd
import numpy as np
from Import import extraction_donnees
import math

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








""" _________________________________________SOUS FONCTIONS UTILES _________________________________________"""

def calculer_capacite_max(vehicule, contenant):
    """
    Calcule le nombre max de contenants dans un véhicule (Bin Packing 2D simple).
    """
    # 1. Vérification de compatibilité (on cherche le nom du contenant dans les colonnes du véhicule)
    nom_cont = contenant['libellé']
    if nom_cont not in vehicule or vehicule[nom_cont] != "OUI":
        return 0

    # 2. Récupération des dimensions
    L_v = vehicule['dim longueur interne (m)']
    l_v = vehicule['dim largeur interne (m)']
    P_max_v = vehicule['Poids max chargement']
    
    L_c = contenant['dim longueur (m)']
    l_c = contenant['dim largeur (m) '] # Attention à l'espace possible dans le nom de colonne
    poids_c = contenant['Poids plein (T)']

    # 3. Calcul du nombre au sol (en testant les deux orientations)
    # Sens A : Longueur contenant sur Longueur véhicule
    nb_sens_A = (L_v // L_c) * (l_v // l_c)
    
    # Sens B : Longueur contenant sur Largeur véhicule (pivotement 90°)
    nb_sens_B = (L_v // l_c) * (l_v // L_c)
    
    nb_max_sol = max(nb_sens_A, nb_sens_B)

    # 4. Vérification de la contrainte de poids
    if poids_c > 0:
        nb_max_poids = P_max_v // poids_c
        capacite_finale = min(nb_max_sol, nb_max_poids)
    else:
        capacite_finale = nb_max_sol

    return int(capacite_finale)
