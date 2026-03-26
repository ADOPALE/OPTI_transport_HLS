import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta
import plotly.express as px
import folium
from streamlit_folium import st_folium



#------ Fonction utilitaire de géocodage------

import requests
import time
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# 1. Géocodage avec Cache pour éviter de taper l'API inutilement
@st.cache_data(show_spinner=False)
def geocode_bio_sites(sites_adresses, hls_adresse):
    """
    sites_adresses : dict { 'NOM': 'ADRESSE' }
    """
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    import time

    geolocator = Nominatim(user_agent="adopale_biologie_nantes")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.1)
    coords_dict = {}

    # 1. Géocodage du dépôt HLS
    try:
        loc_hls = geocode(hls_adresse)
        if loc_hls:
            coords_dict["HLS"] = {"lat": loc_hls.latitude, "lon": loc_hls.longitude}
    except:
        pass

    # 2. Géocodage des sites
    for site, addr in sites_adresses.items():
        if addr and site not in coords_dict:
            try:
                loc = geocode(addr)
                if loc:
                    coords_dict[site.upper()] = {"lat": loc.latitude, "lon": loc.longitude}
                time.sleep(0.1)
            except:
                continue
    return coords_dict

# 2. Récupération du tracé routier réel via OSRM
def get_route_osrm(waypoints):
    """ waypoints: liste de dict [{'lat': x, 'lon': y}] """
    if len(waypoints) < 2: return None
    
    coords_str = ";".join(f"{wp['lon']},{wp['lat']}" for wp in waypoints)
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("code") == "Ok":
            return [[c[1], c[0]] for c in data["routes"][0]["geometry"]["coordinates"]]
    except:
        return None
    return None
#--------------------




def afficher_stats_vehicules(flotte, df_dist):
    """
    Calcule les KPIs et affiche le graphique d'occupation des véhicules
    avec alternance de couleurs Bleu/Orange par chauffeur.
    """
    st.subheader("🚐 Données sur les véhicules")
    
    # --- 1. NETTOYAGE DE LA MATRICE DE DISTANCE ---
    df_dist_clean = df_dist.copy()
    # On définit la première colonne comme index (noms des sites)
    nom_col_sites = df_dist_clean.columns[0]
    df_dist_clean = df_dist_clean.set_index(nom_col_sites)
    # Nettoyage des index et colonnes (Majuscules et sans espaces)
    df_dist_clean.index = df_dist_clean.index.astype(str).str.strip().str.upper()
    df_dist_clean.columns = df_dist_clean.columns.astype(str).str.strip().str.upper()

    # --- 2. CALCUL DES INDICATEURS ET PRÉPARATION GRAPHIQUE ---
    nb_vehicules = len(flotte)
    km_totaux = 0
    nb_chauffeurs = 0
    
    # Couleurs demandées pour l'alternance des chauffeurs
    COULEURS_CHAUFFEURS = ["#2E86C1", "#EB984E"] # Bleu / Orange
    
    fig = go.Figure()

    for v_id, vacations in flotte.items():
        nb_chauffeurs += len(vacations)
        
        for v_idx, vacation in enumerate(vacations):
            # Sélection de la couleur selon l'index du chauffeur (0=Bleu, 1=Orange...)
            couleur_actuelle = COULEURS_CHAUFFEURS[v_idx % len(COULEURS_CHAUFFEURS)]
            
            for tournee in vacation:
                # Calcul des kilomètres de la tournée
                for i in range(len(tournee) - 1):
                    s_dep = str(tournee[i]['site']).strip().upper()
                    s_arr = str(tournee[i+1]['site']).strip().upper()
                    
                    try:
                        km_totaux += df_dist_clean.loc[s_dep, s_arr]
                    except KeyError:
                        pass # On ignore si la distance est introuvable pour le KPI

                # Ajout du segment au graphique
                debut = tournee[0]['heure']
                fin = tournee[-1]['heure']
                
                fig.add_trace(go.Bar(
                    base=[debut],
                    x=[fin - debut],
                    y=[v_id],
                    orientation='h',
                    marker_color=couleur_actuelle,
                    name=f"Chauffeur {v_idx + 1}",
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{v_id}</b><br>"
                        f"Chauffeur n°{v_idx + 1}<br>"
                        f"Horaire: {str(timedelta(minutes=debut))[:-3]} - {str(timedelta(minutes=fin))[:-3]}"
                        "<extra></extra>"
                    )
                ))

    # --- 3. AFFICHAGE DES METRICS ---
    km_moyen_chauffeur = km_totaux / nb_chauffeurs if nb_chauffeurs > 0 else 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Véhicules nécessaires", f"{nb_vehicules}")
    c2.metric("Distance totale", f"{int(km_totaux)} km")
    c3.metric("Km moyen / chauffeur", f"{int(km_moyen_chauffeur)} km")

    # --- 4. MISE EN FORME DU GRAPHIQUE ---
    fig.update_layout(
        title="Occupation temporelle des véhicules (par chauffeur)",
        xaxis=dict(
            title="Heure de la journée",
            tickvals=list(range(300, 1321, 60)), # De 5h à 22h
            ticktext=[f"{h//60}h" for h in range(300, 1321, 60)],
            range=[300, 1320] 
        ),
        yaxis=dict(autorange="reversed"), # Pour avoir Véhicule 1 en haut
        barmode='stack',
        height=400 + (nb_vehicules * 25),
        margin=dict(l=10, r=10, t=50, b=50)
    )

    st.plotly_chart(fig, width='stretch')


def afficher_stats_chauffeurs(flotte, config_rh):
    """
    Calcule les indicateurs de performance liés aux chauffeurs (vacations).
    """
    st.subheader("👥 Données sur les chauffeurs")

    nb_postes = 0
    duree_totale_tournees = 0
    total_tournees = 0
    
    # Récupération des contraintes RH
    # Amplitude (ex: 450 min), Pause (ex: 30 min)
    amplitude_max = config_rh.get('amplitude', 450)
    pause_reglementaire = config_rh.get('pause', 30)

    for v_id, vacations in flotte.items():
        nb_postes += len(vacations) # Chaque vacation est un poste chauffeur
        
        for vacation in vacations:
            total_tournees += len(vacation)
            for tournee in vacation:
                # Durée de la tournée = Heure de fin - Heure de début
                duree_trne = tournee[-1]['heure'] - tournee[0]['heure']
                duree_totale_tournees += duree_trne

    # --- CALCUL DES INDICATEURS ---
    
    # 1. Taux d'occupation moyen
    # Formule : Temps de roulage / (Amplitude totale - Temps de pause)
    temps_travail_dispo_par_poste = amplitude_max - pause_reglementaire
    if nb_postes > 0 and temps_travail_dispo_par_poste > 0:
        occupation_moyenne = (duree_totale_tournees / (nb_postes * temps_travail_dispo_par_poste)) * 100
    else:
        occupation_moyenne = 0

    # 2. Moyenne de tournées par poste
    tournees_par_poste = total_tournees / nb_postes if nb_postes > 0 else 0

    # --- AFFICHAGE ---
    c1, c2, c3 = st.columns(3)
    c1.metric("Nombre de postes (7h30)", f"{nb_postes}")
    c2.metric("Taux d'occupation moyen", f"{occupation_moyenne:.1f} %")
    c3.metric("Tournées moyennes / poste", f"{tournees_par_poste:.1f}")

    # Petit graphique de répartition du temps pour un chauffeur type
    if nb_postes > 0:
        temps_moyen_roulage = duree_totale_tournees / nb_postes
        temps_inoccupé = temps_travail_dispo_par_poste - temps_moyen_roulage
        
        fig_pie = px.pie(
            names=["Temps en tournée", "Temps inoccupé / Attente", "Pause réglementaire"],
            values=[temps_moyen_roulage, max(0, temps_inoccupé), pause_reglementaire],
            color_discrete_sequence=["#2E86C1", "#D5D8DC", "#EB984E"],
            title="Répartition moyenne d'une vacation"
        )
        st.plotly_chart(fig_pie, use_container_width=True)



def afficher_stats_sites(flotte):
    """
    Affiche les statistiques par site et le graphique temporel des passages.
    """
    st.subheader("🏥 Données sur les sites collectés")

    passages_data = []
    total_tournees = 0

    # 1. Extraction des passages depuis la structure de la flotte
    for v_id, vacations in flotte.items():
        for vacation in vacations:
            for tournee in vacation:
                total_tournees += 1
                for point in tournee:
                    site = point['site']
                    # On ignore le dépôt HLS pour le graphique des sites périphériques
                    if site != "HLS":
                        h_total = point['heure']
                        # On prépare déjà le texte de l'heure pour l'infobulle
                        h_str = f"{int(h_total//60):02d}:{int(h_total%60):02d}"
                        
                        passages_data.append({
                            "Site": site,
                            "Heure_Num": h_total, # Pour la position X
                            "Heure_Texte": h_str,  # Pour l'affichage bulle
                            "Véhicule": v_id
                        })

    if not passages_data:
        st.warning("Aucun passage sur site détecté (hors HLS).")
        return

    # À ajouter temporairement pour débugger
    print(f"DEBUG: Sites trouvés dans la simulation : {set([p['site'] for v in flotte.values() for vac in v for trne in vac for p in trne])}")
    
    df_passages = pd.DataFrame(passages_data)

    # 2. Affichage du KPI global
    st.metric("Nombre total de tournées réalisées", f"{total_tournees}")

    # 3. Graphique de dispersion (Scatter Plot) des passages
    st.write("**Horaires de passage par site**")

    fig_sites = px.scatter(
        df_passages,
        x="Heure_Num",
        y="Site",
        color="Véhicule",
        symbol="Véhicule",
        # On attache les données propres au point
        custom_data=["Heure_Texte", "Véhicule"],
        title="Répartition des collectes sur la journée"
    )

    # 4. Personnalisation UNIQUE de l'affichage et de l'infobulle
    fig_sites.update_traces(
        marker=dict(size=12, opacity=0.8),
        hovertemplate=(
            "<b>%{y}</b><br>" +
            "Passage à <b>%{customdata[0]}</b><br>" +
            "Véhicule : %{customdata[1]}" +
            "<extra></extra>"
        )
    )

    # 5. Configuration des axes et du layout
    fig_sites.update_layout(
        xaxis=dict(
            title="Heure de passage",
            tickvals=list(range(300, 1321, 60)),
            ticktext=[f"{h//60}h" for h in range(300, 1321, 60)],
            range=[300, 1320]
        ),
        yaxis=dict(
            title=None, 
            categoryorder='category ascending'
        ),
        height=400 + (len(df_passages["Site"].unique()) * 20),
        margin=dict(l=10, r=10, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    st.plotly_chart(fig_sites, use_container_width=True)


def afficher_detail_flotte_vehicules(flotte, df_dist):
    st.subheader("🚐 Synthèse par véhicule")

    # 1. Sélection du véhicule via menu déroulant
    liste_vehicules = list(flotte.keys())
    vehicule_selectionne = st.selectbox("Sélectionnez un véhicule pour voir le détail", liste_vehicules)

    if vehicule_selectionne:
        vacations = flotte[vehicule_selectionne]
        
        # --- CALCULS ---
        # Nettoyage rapide de la matrice pour le calcul des km
        df_dist_clean = df_dist.copy()
        nom_col = df_dist_clean.columns[0]
        df_dist_clean = df_dist_clean.set_index(nom_col)
        df_dist_clean.index = df_dist_clean.index.astype(str).str.strip().str.upper()
        df_dist_clean.columns = df_dist_clean.columns.astype(str).str.strip().str.upper()

        km_jour = 0
        nb_tournees = 0
        gantt_data = []
        tournee_index = 1 # Pour la numérotation chronologique

        for v_idx, vacation in enumerate(vacations):
            for tournee in vacation:
                nb_tournees += 1
                # Calcul distance
                for i in range(len(tournee) - 1):
                    s_dep = str(tournee[i]['site']).strip().upper()
                    s_arr = str(tournee[i+1]['site']).strip().upper()
                    try:
                        km_jour += df_dist_clean.loc[s_dep, s_arr]
                    except KeyError:
                        pass
                
                # Préparation Graphe
                gantt_data.append({
                    "ID": f"T{tournee_index}",
                    "Début": tournee[0]['heure'],
                    "Fin": tournee[-1]['heure'],
                    "Chauffeur": f"Chauffeur {v_idx + 1}",
                    "Couleur": "#2E86C1" if v_idx % 2 == 0 else "#EB984E"
                })
                tournee_index += 1

        # --- AFFICHAGE DES METRICS ---
        km_an = km_jour * 5 * 52
        c1, c2 = st.columns(2)
        c1.metric("Distance parcourue", f"{int(km_jour)} km / jour", f"{int(km_an):,} km / an")
        c2.metric("Nombre de tournées", f"{nb_tournees} tournées / jour")

        # --- GRAPHE MONO-LIGNE NUMÉROTÉ ---
        fig = go.Figure()
        
        for item in gantt_data:
            fig.add_trace(go.Bar(
                base=[item["Début"]],
                x=[item["Fin"] - item["Début"]],
                y=[vehicule_selectionne],
                orientation='h',
                marker_color=item["Couleur"],
                text=item["ID"], # Numérotation T1, T2...
                textposition='inside',
                insidetextanchor='middle',
                textfont=dict(color="white", size=14),
                name=item["Chauffeur"],
                hovertemplate=f"<b>{item['ID']}</b><br>{item['Chauffeur']}<br>Durée: %{{x}} min<extra></extra>"
            ))

        fig.update_layout(
            height=200,
            xaxis=dict(
                title="Heure",
                tickvals=list(range(300, 1321, 60)),
                ticktext=[f"{h//60}h" for h in range(300, 1321, 60)],
                range=[300, 1320]
            ),
            yaxis=dict(showticklabels=True),
            showlegend=False,
            margin=dict(l=10, r=10, t=40, b=40),
            barmode='stack'
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        return vehicule_selectionne, vacations # On retourne ces infos pour l'ensemble suivant




def afficher_detail_itineraire(v_id, vacations, sites_config, hls_adresse):
    # 1. Création de la liste des tournées pour le menu
    tous_les_passages = []
    index_tournee = 1
    for v_idx, vac in enumerate(vacations):
        for trne in vac:
            tous_les_passages.append({
                "label": f"Tournée {index_tournee} (Chauffeur {v_idx+1})",
                "data": trne,
                "chauffeur": v_idx + 1
            })
            index_tournee += 1

    st.write("---")
    col_sel, _ = st.columns([2, 1])
    with col_sel:
        selection = st.selectbox("📍 Choisir une tournée à inspecter", tous_les_passages, format_func=lambda x: x["label"])

    if selection:
        trne_data = selection["data"]
        st.write(f"#### ⏱️ Journal de bord : {selection['label']}")
        
        # 2. Tableau des passages
        tableau = []
        for i, p in enumerate(trne_data):
            h_total = p.get('heure', 0)
            h_str = f"{int(h_total//60):02d}:{int(h_total%60):02d}"
            type_arret = "🚀 Départ HLS" if i == 0 else ("🏁 Dépôt (Retour)" if i == len(trne_data)-1 else "🏥 Collecte")
            tableau.append({
                "Ordre": i + 1,
                "Site": str(p.get('site', 'Inconnu')).upper(),
                "Heure de passage": h_str,
                "Type": type_arret
            })
        st.table(pd.DataFrame(tableau).set_index("Ordre"))

        # 3. PARTIE CARTE AVEC TRACÉ ROUTIER (OSRM)
        st.write("#### 🗺️ Itinéraire géographique (Tracé routier)")
        show_map = st.checkbox("🗺️ Charger la carte interactive", value=False)
    
        if show_map:
            with st.spinner("🌍 Calcul de l'itinéraire routier..."):
                coords_gps = geocode_bio_sites(sites_config, hls_adresse)
                
                # Préparation des points (waypoints) pour OSRM
                waypoints_osrm = []
                for stop in trne_data:
                    nom_site = str(stop.get('site', '')).upper() if isinstance(stop, dict) else str(stop).upper()
                    if nom_site in coords_gps:
                        waypoints_osrm.append({
                            "lat": coords_gps[nom_site]["lat"], 
                            "lon": coords_gps[nom_site]["lon"], 
                            "nom": nom_site
                        })
                
                if len(waypoints_osrm) >= 2:
                    # APPEL À OSRM POUR LE TRACÉ RÉEL
                    route_line = get_route_osrm(waypoints_osrm)
                    
                    m = folium.Map(location=[waypoints_osrm[0]['lat'], waypoints_osrm[0]['lon']], zoom_start=12, tiles="CartoDB positron")
                    
                    if route_line:
                        # On dessine le tracé ROUTIER
                        folium.PolyLine(route_line, color="#2E86C1", weight=5, opacity=0.8).add_to(m)
                    else:
                        # Backup ligne droite si OSRM échoue
                        folium.PolyLine([[w['lat'], w['lon']] for w in waypoints_osrm], color="#2E86C1", weight=2, dash_array='5').add_to(m)
                    
                    # Ajout des marqueurs
                    for i, wp in enumerate(waypoints_osrm):
                        folium.Marker([wp['lat'], wp['lon']], tooltip=f"{i+1}. {wp['nom']}", 
                                      icon=folium.Icon(color="blue", icon="info-sign")).add_to(m)
                    
                    st_folium(m, width='stretch', height=500, returned_objects=[])
                else:
                    st.warning("📍 Pas assez de points géocodés pour tracer la route.")
