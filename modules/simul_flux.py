import streamlit as st
import pandas as pd
import numpy as np
from modules.Import import extraction_donnees
import math


"""
FONCTION - SEGMENTER_FLUX
Sépare les flux 'Volume' en Récurrents (Lundi au Vendredi) et Spécifiques.
"""
def segmenter_flux(df):
    # 1. Nom exact de la colonne de nature du flux (pour distinguer volumes et fréquences)
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



"""
FONCTION - CHOIX_JMAX
cette fonction permet de stabiliser le besoin de transport récurrent en intégrant une marge pour être sur qu'avec les flux du JMax on sera bien capable de transporter tous les autres jours. 
"""
def choix_Jmax(df_recurrent, df_vehicules, df_contenants, matrice_duree, df_sites):
    # --- PRÉPARATION DES COLONNES ---
    jours_cols = ["Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", 
                  "Quantité Jeudi", "Quantité Vendredi", "Quantité Samedi", "Quantité Dimanche"]

    #6 min de mise à quai (manoeuvre (temps arbitraire) 
    t_mise_quai = 6.0  

    # ---INITIALISATION---- 
    poids_totaux_par_jour = {j: 0.0 for j in jours_cols}
    
    # --- NETTOYAGE DES RÉFÉRENTIELS (SÉCURISÉ) ---
    # 1. Nettoyage des colonnes de df_sites
    df_sites.columns = [str(c).strip().upper() for c in df_sites.columns]
    
    # 2. Identification dynamique de la colonne 'Libellé'
    col_libelle = next((c for c in df_sites.columns if "LIBEL" in c or "SITE" in c), None)
    
    if col_libelle:
        df_sites[col_libelle] = df_sites[col_libelle].astype(str).str.strip().str.upper()
    else:
        st.error("❌ Impossible de trouver la colonne 'Libellé' dans param_sites.")
        return df_recurrent

    # 3. Identification dynamique de la colonne 'Quai'
    col_quai = next((c for c in df_sites.columns if "QUAI" in c), "PRÉSENCE DE QUAI")

    # --- PRÉPARATION MATRICE ---
    matrice_duree.columns = [str(c).strip().upper() for c in matrice_duree.columns]
    col_sites_depart = matrice_duree.columns[0]
    matrice_duree[col_sites_depart] = matrice_duree[col_sites_depart].astype(str).str.strip().str.upper()

    # --- ÉTAPE 1 : CALCUL DES POIDS FICTIFS ---
    for _, flux in df_recurrent.iterrows():
        site_dep = str(flux['Point de départ']).strip().upper()
        site_arr = str(flux['Point de destination']).strip().upper()
        type_cont = str(flux['Nature de contenant']).strip().upper()
        
        try:
            cont_info = df_contenants[df_contenants['libellé'].str.strip().str.upper() == type_cont].iloc[0]
        except:
            continue

        meilleure_capa = 0
        v_elu = None

        for _, v in df_vehicules.iterrows():
            type_vehicule = str(v['Types']).strip().upper()
            try:
                # Utilisation de col_libelle au lieu de 'Libellé' fixe
                acc_dep = df_sites.loc[df_sites[col_libelle] == site_dep, type_vehicule].values[0]
                acc_arr = df_sites.loc[df_sites[col_libelle] == site_arr, type_vehicule].values[0]
                
                if acc_dep == "OUI" and acc_arr == "OUI":
                    capa = calculer_capacite_max(v, cont_info)
                    if capa > meilleure_capa:
                        meilleure_capa = capa
                        v_elu = v
            except:
                continue

        if v_elu is not None and meilleure_capa > 0:
            try:
                # Recherche manuelle ligne par ligne
                ligne_matrice = matrice_duree[matrice_duree[col_sites_depart] == site_dep]
                
                if not ligne_matrice.empty and site_arr in matrice_duree.columns:
                    duree = ligne_matrice[site_arr].values[0]
                    
                    # Détermination quai avec col_quai dynamique
                    a_quai_rows = df_sites.loc[df_sites[col_libelle] == site_arr, col_quai]
                    a_quai = a_quai_rows.values[0] == "OUI" if not a_quai_rows.empty else False
                    
                    col_m = 'Manutention avec quai (minutes / contenants)' if a_quai else 'Manutention sans quai (minutes / contenants)'
                    t_manut = to_decimal_minutes(v_elu[col_m])

                    for j in jours_cols:
                        qte = flux[j]
                        if qte > 0:
                            nb_trajets = math.ceil(qte / meilleure_capa)
                            poids = (duree + (meilleure_capa * t_manut) + t_mise_quai) * nb_trajets
                            poids_totaux_par_jour[j] += poids
            except:
                continue

    # --- ÉTAPE 2 : IDENTIFICATION JMAX ET RÈGLE DES 10% ---
    if all(v == 0 for v in poids_totaux_par_jour.values()):
        j_max_nom = "Quantité Lundi"
        st.warning("⚠️ Poids calculés à 0.0. Vérifiez la correspondance des noms de sites.")
    else:
        j_max_nom = max(poids_totaux_par_jour, key=poids_totaux_par_jour.get)
   
    
    # Construction du texte de détail (ex: Lun: 120min | Mar: 150min...)
    # On enlève "Quantité " du nom pour que ce soit plus court à l'affichage
    detail_jours = " | ".join([f"{k.replace('Quantité ', '')}: {round(v, 1)} min" for k, v in poids_totaux_par_jour.items()])
    
    # Affichage complet
    st.info(f"📊 **Jmax identifié** : {j_max_nom.replace('Quantité ', '')}")
    st.caption(f"⚖️ **Détail des charges calculées :**\n\n{detail_jours}")


    def appliquer_regle_marge(row):
        val_j_max = row[j_max_nom]
        val_max_semaine = max([row[j] for j in jours_cols])
        return val_max_semaine if val_max_semaine > (val_j_max * 1.10) else val_j_max

    df_sequence_type = df_recurrent.copy()
    df_sequence_type['Quantité_Séquence_Type'] = df_sequence_type.apply(appliquer_regle_marge, axis=1)
    
    return df_sequence_type








""" _________________________________________SOUS FONCTIONS UTILES _________________________________________"""

"""
Calcule le nombre maximum absolu de contenants dans un véhicule 
en testant des agencements complexes (orientations mixtes).
"""
import math

def calculer_capacite_max(vehicule, contenant):
    """
    Calcule le nombre maximum absolu de contenants dans un véhicule.
    Nettoie les noms de colonnes et utilise un bin-packing 2D optimisé.
    """
    # 1. Nettoyage des noms de colonnes (enlève les espaces invisibles)
    # On transforme les Series en dictionnaires avec clés "propres"
    v = {str(k).strip(): val for k, val in vehicule.items()}
    c = {str(k).strip(): val for k, val in contenant.items()}

    # 2. Vérification de compatibilité
    nom_cont = c.get('libellé')
    # On vérifie si le véhicule accepte ce contenant (colonne à "OUI")
    if not nom_cont or v.get(nom_cont) != "OUI":
        return 0

    # 3. Récupération des dimensions et contraintes
    try:
        L_v = v['dim longueur interne (m)']
        l_v = v['dim largeur interne (m)']
        P_max_v = v['Poids max chargement']
        
        L_c = c['dim longueur (m)']
        l_c = c['dim largeur (m)']
        poids_c = c['Poids plein (T)']
    except KeyError as e:
        # En cas de colonne vraiment manquante, on lève une erreur explicite
        raise KeyError(f"Erreur : La colonne {e} est introuvable dans les paramètres.")

    # 4. Moteur de calcul récursif (Guillotine Cut) pour maximiser le remplissage au sol
    def solve_max(L, l, w, h, memo):
        # Si l'espace est trop petit pour le contenant (dans les deux sens)
        if (L < w and L < h) or (l < w and l < h):
            return 0
        
        state = (round(L, 3), round(l, 3))
        if state in memo:
            return memo[state]
        
        res = 0
        # Test orientation A (Normal)
        if L >= w and l >= h:
            optA = 1 + solve_max(L - w, l, w, h, memo) + solve_max(w, l - h, w, h, memo)
            optB = 1 + solve_max(L, l - h, w, h, memo) + solve_max(L - w, h, w, h, memo)
            res = max(res, optA, optB)
            
        # Test orientation B (Pivoté 90°)
        if L >= h and l >= w:
            optA_rot = 1 + solve_max(L - h, l, w, h, memo) + solve_max(h, l - w, w, h, memo)
            optB_rot = 1 + solve_max(L, l - w, w, h, memo) + solve_max(L - h, w, h, h, memo)
            res = max(res, optA_rot, optB_rot)
            
        memo[state] = res
        return res

    # Calcul du maximum au sol
    nb_max_sol = solve_max(L_v, l_v, L_c, l_c, {})

    # 5. Limitation par le poids maximum autorisé
    if poids_c > 0:
        nb_max_poids = int(P_max_v // poids_c)
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
