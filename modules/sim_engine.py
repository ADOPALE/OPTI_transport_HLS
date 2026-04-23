import pandas as pd
import numpy as np
import math
import streamlit as st
from datetime import datetime, time

# --- IMPORTS OR-TOOLS (Le coeur du moteur) ---
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

# --- IMPORT DE TES FONCTIONS EXISTANTES ---
# On importe les fonctions de calcul de capacité et de conversion 
# depuis ton fichier original pour éviter de les dupliquer
from modules.Prep_simul_flux import calculer_capacite_max, to_decimal_minutes


import pandas as pd
import numpy as np

def create_data_model(df_sequence_type, df_sites, df_vehicules, df_contenants, matrice_duree, h_prise_min, h_fin_max):
    """
    Prépare le dictionnaire de données pour OR-Tools.
    Incorpore les sites, la matrice des temps, les fenêtres horaires et les 
    contraintes de surface au sol pour le Bin-Packing.
    """
    data = {}
    
    # --- 1. MAPPING DES SITES ---
    # On récupère les noms uniques des sites
    sites_uniques = list(df_sites[df_sites.columns[0]].unique())
    # On s'assure que le dépôt central (HSJ) est à l'indice 0
    if "HSJ" in sites_uniques:
        sites_uniques.remove("HSJ")
        sites_uniques.insert(0, "HSJ")
    
    data['locations_names'] = sites_uniques
    site_to_index = {name: i for i, name in enumerate(sites_uniques)}
    data['depot'] = 0

    # --- 2. MATRICE DES TEMPS (en minutes) ---
    num_locations = len(sites_uniques)
    time_matrix = np.zeros((num_locations, num_locations))
    for i, loc_i in enumerate(sites_uniques):
        for j, loc_j in enumerate(sites_uniques):
            if i == j:
                time_matrix[i][j] = 0
            else:
                try:
                    # Recherche dans la matrice excel chargée
                    val = matrice_duree.loc[matrice_duree[matrice_duree.columns[0]] == loc_i, loc_j].values[0]
                    time_matrix[i][j] = int(val)
                except:
                    # Si trajet manquant (ex: nouveaux quais HSJ), 10 min par défaut pour manoeuvre
                    time_matrix[i][j] = 10
    data['time_matrix'] = time_matrix.tolist()

    # --- 3. FENÊTRES HORAIRES (Time Windows) ---
    # Conversion des horaires globaux RH en minutes
    start_global = to_decimal_minutes(h_prise_min)
    end_global = to_decimal_minutes(h_fin_max)
    
    # Par défaut, chaque site est ouvert sur la plage RH
    data['time_windows'] = [(int(start_global), int(end_global)) for _ in sites_uniques]

    # --- 4. PRE-CALCUL DES SURFACES ET DIMENSIONS DES CONTENANTS ---
    # On crée un dictionnaire pour accès rapide {(Nom): {'surf': X, 'L': Y, 'l': Z}}
    cont_info_map = {}
    for _, c in df_contenants.iterrows():
        nom = str(c['libellé']).strip().upper()
        longueur = c['dim longueur (m)']
        largeur = c['dim largeur (m)']
        poids = c.get('Poids plein (T)', 0) # Si tu gères aussi le poids
        
        cont_info_map[nom] = {
            'surface': longueur * largeur,
            'longueur': longueur,
            'largeur': largeur,
            'poids': poids
        }

    # --- 5. DEFINITION DES MISSIONS (Pickups & Deliveries) ---
    pickups_deliveries = []
    
    for _, flux in df_sequence_type.iterrows():
        try:
            origin_name = str(flux['Point de départ']).strip().upper()
            dest_name = str(flux['Point de destination']).strip().upper()
            
            origin_idx = site_to_index[origin_name]
            dest_idx = site_to_index[dest_name]
            
            # Fenêtres spécifiques au flux (si renseignées)
            h_dep = to_decimal_minutes(flux.get("Heure de mise à disposition min départ")) if pd.notna(flux.get("Heure de mise à disposition min départ")) else start_global
            h_arr = to_decimal_minutes(flux.get("Heure max de livraison à la destination")) if pd.notna(flux.get("Heure max de livraison à la destination")) else end_global
            
            # Infos contenant
            type_cont = str(flux['Nature de contenant']).strip().upper()
            info_c = cont_info_map.get(type_cont, {'surface': 1.0, 'longueur': 1.0, 'largeur': 1.0})
            
            qte = int(flux['Quantité_Séquence_Type'])
            for _ in range(qte):
                pickups_deliveries.append({
                    'pickup': origin_idx,
                    'delivery': dest_idx,
                    'window_pickup': (int(h_dep), int(end_global)),
                    'window_delivery': (int(start_global), int(h_arr)),
                    'surface': info_c['surface'],
                    'dims': (info_c['longueur'], info_c['largeur']),
                    'nom_contenant': type_cont
                })
        except KeyError as e:
            st.error(f"Erreur de mapping site dans le flux : {e}")
            continue

    data['pickups_deliveries'] = pickups_deliveries

    # --- 6. FLOTTE DE VÉHICULES ---
    vehicle_capacities_surf = []
    # On déploie la flotte selon df_vehicules
    for _, v in df_vehicules.iterrows():
        surface_utile = v['Surface utile (m2)']
        nombre = int(v['Nombre'])
        for _ in range(nombre):
            vehicle_capacities_surf.append(float(surface_utile))
            
    data['vehicle_capacities'] = vehicle_capacities_surf
    data['num_vehicles'] = len(vehicle_capacities_surf)
    
    return data





def resoudre_vrp(data):
    # --- 1. INITIALISATION ---
    # manager gère la correspondance entre les sites et les indices internes
    manager = pywrapcp.RoutingIndexManager(
        len(data['time_matrix']), 
        data['num_vehicles'], 
        data['depot']
    )
    routing = pywrapcp.RoutingModel(manager)

    # Fonction de coût : le temps de trajet
    def transit_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return data['time_matrix'][from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # --- 2. DIMENSION TEMPS (Fenêtres horaires) ---
    time_dimension_name = 'Time'
    routing.AddDimension(
        transit_callback_index,
        60,    # Temps d'attente autorisé à un quai (slack)
        1440,  # Capacité max du véhicule en minutes (24h)
        False, # Ne pas forcer le départ à zéro
        time_dimension_name
    )
    time_dimension = routing.GetDimensionOrDie(time_dimension_name)

    # Ajouter les contraintes de fenêtres horaires pour chaque site
    for location_idx, window in enumerate(data['time_windows']):
        index = manager.NodeToIndex(location_idx)
        time_dimension.CumulVar(index).SetRange(window[0], window[1])

    # --- 3. GESTION DES PICKUPS & DELIVERIES ---
    for request in data['pickups_deliveries']:
        pickup_index = manager.NodeToIndex(request['pickup'])
        delivery_index = manager.NodeToIndex(request['delivery'])
        
        # On lie le pickup et la livraison pour qu'ils soient faits par le même véhicule
        routing.AddPickupAndDelivery(pickup_index, delivery_index)
        routing.solver().Add(
            routing.VehicleVar(pickup_index) == routing.VehicleVar(delivery_index)
        )
        # La livraison doit avoir lieu APRÈS le pickup
        routing.solver().Add(
            time_dimension.CumulVar(pickup_index) <= time_dimension.CumulVar(delivery_index)
        )

    # --- À SUIVRE : LA DIMENSION CAPACITÉ BIN PACKING ---
    return manager, routing, time_dimension
