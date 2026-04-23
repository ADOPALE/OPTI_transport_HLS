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

"""
Calcule le nombre maximum absolu de contenants dans un véhicule 
en testant des agencements complexes (orientations mixtes).
"""
def calculer_capacite_max(vehicule, contenant):
    # 1. Vérification de compatibilité
    nom_cont = contenant['libellé']
    # On vérifie dans le tableau véhicule si la colonne du contenant est à "OUI"
    if nom_cont not in vehicule or vehicule[nom_cont] != "OUI":
        return 0

    # 2. Récupération des dimensions et contraintes
    L_v = vehicule['dim longueur interne (m)']
    l_v = vehicule['dim largeur interne (m)']
    P_max_v = vehicule['Poids max chargement']
    
    L_c = contenant['dim longueur (m)']
    l_c = contenant['dim largeur (m) '] # Note: respect de l'espace dans ton Excel
    poids_c = contenant['Poids plein (T)']

    # 3. Moteur de calcul récursif pour le remplissage optimal (Guillotine Cut)
    def solve_max(L, l, w, h, memo):
        # On ne peut pas placer le contenant si l'espace est trop petit
        if (L < w and L < h) or (l < w and l < h):
            return 0
        
        # Utilisation de la mémoïsation pour accélérer le calcul
        state = (L, l)
        if state in memo:
            return memo[state]
        
        res = 0
        # Option 1 : On place le contenant dans le sens normal (w x h)
        if L >= w and l >= h:
            # On découpe l'espace restant soit horizontalement soit verticalement
            # Découpe A : un rectangle à côté (L-w x l) et un devant (w x l-h)
            optA = 1 + solve_max(L - w, l, w, h, memo) + solve_max(w, l - h, w, h, memo)
            # Découpe B : un rectangle devant (L x l-h) et un à côté (L-w x h)
            optB = 1 + solve_max(L, l - h, w, h, memo) + solve_max(L - w, h, w, h, memo)
            res = max(res, optA, optB)
            
        # Option 2 : On place le contenant pivoté à 90° (h x w)
        if L >= h and l >= w:
            optA_rot = 1 + solve_max(L - h, l, w, h, memo) + solve_max(h, l - w, w, h, memo)
            optB_rot = 1 + solve_max(L, l - w, w, h, memo) + solve_max(L - h, w, h, h, memo)
            res = max(res, optA_rot, optB_rot)
            
        memo[state] = res
        return res

    # Calcul du nombre max au sol
    nb_max_sol = solve_max(L_v, l_v, L_c, l_c, {})

    # 4. Vérification de la contrainte de poids
    if poids_c > 0:
        # Le poids max chargement est en Tonnes, tout comme le poids plein du contenant
        nb_max_poids = P_max_v // poids_c
        capacite_finale = min(nb_max_sol, nb_max_poids)
    else:
        capacite_finale = nb_max_sol

    return int(capacite_finale)


"""Convertit un objet time ou une chaîne HH:MM:SS en minutes décimales."""
def to_decimal_minutes(time_val):
    if isinstance(time_val, str):
        h, m, s = map(int, time_val.split(':'))
        return h * 60 + m + s / 60
    elif hasattr(time_val, 'hour'):
        return time_val.hour * 60 + time_val.minute + time_val.second / 60
    return float(time_val)



"""Vérifie si le véhicule peut circuler sur les deux sites."""
def est_accessible(vehicule_nom, site_depart, site_arrivee, df_sites):
    try:
        # Récupération des lignes des sites
        acc_dep = df_sites.loc[df_sites['Libellé'] == site_depart, vehicule_nom].values[0]
        acc_arr = df_sites.loc[df_sites['Libellé'] == site_arrivee, vehicule_nom].values[0]
        return acc_dep == "OUI" and acc_arr == "OUI"
    except:
        return False
