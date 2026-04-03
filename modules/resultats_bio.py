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

def minutes_to_hhmm(minutes):
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

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




def afficher_stats_vehicules(postes, df_dist):
    """
    Calcule les KPIs et affiche le graphique d'occupation.
    S'adapte à la nouvelle structure : { 'CH_01': [tournee1, tournee2], ... }
    """
    st.subheader("🚐 Données sur les véhicules et chauffeurs")
    
    # --- 1. NETTOYAGE DE LA MATRICE DE DISTANCE ---
    df_dist_clean = df_dist.copy()
    nom_col_sites = df_dist_clean.columns[0]
    df_dist_clean = df_dist_clean.set_index(nom_col_sites)
    df_dist_clean.index = df_dist_clean.index.astype(str).str.strip().str.upper()
    df_dist_clean.columns = df_dist_clean.columns.astype(str).str.strip().str.upper()

    # --- 2. CALCUL DES INDICATEURS ET PRÉPARATION GRAPHIQUE ---
    nb_chauffeurs = len(postes)
    km_totaux = 0
    
    # Couleurs pour l'alternance visuelle (ici on peut alterner par chauffeur)
    COULEURS = ["#2E86C1", "#EB984E"] 
    
    fig = go.Figure()

    # On itère sur chaque chauffeur (ou véhicule selon votre renommage)
    for idx, (c_id, liste_tournees) in enumerate(postes.items()):
        couleur_actuelle = COULEURS[idx % len(COULEURS)]
        
        # Chaque 'liste_tournees' est une liste de tournées unitaires
        for t_idx, tournee in enumerate(liste_tournees):
            # CALCUL DES KM
            # tournee est une liste de dict : [{'site': 'HLS', 'heure': 540}, ...]
            for i in range(len(tournee) - 1):
                try:
                    s_dep = str(tournee[i]['site']).strip().upper()
                    s_arr = str(tournee[i+1]['site']).strip().upper()
                    if s_dep in df_dist_clean.index and s_arr in df_dist_clean.columns:
                        km_totaux += df_dist_clean.loc[s_dep, s_arr]
                except (KeyError, IndexError):
                    pass

            # AJOUT AU GRAPHIQUE (Gantt)
            debut = tournee[0]['heure']
            fin = tournee[-1]['heure']
            
            fig.add_trace(go.Bar(
                base=[debut],
                x=[fin - debut],
                y=[c_id],
                orientation='h',
                marker_color=couleur_actuelle,
                name=c_id,
                showlegend=False,
                hovertemplate=(
                    f"<b>{c_id}</b><br>"
                    f"Tournée n°{t_idx + 1}<br>"
                    f"Horaire: {str(timedelta(minutes=debut))[:-3]} - {str(timedelta(minutes=fin))[:-3]}"
                    "<extra></extra>"
                )
            ))

    # --- 3. AFFICHAGE DES METRICS ---
    # Ici, nb_chauffeurs est égal au nombre de clés dans le dictionnaire 'postes'
    km_moyen_chauffeur = km_totaux / nb_chauffeurs if nb_chauffeurs > 0 else 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Effectif Chauffeurs", f"{nb_chauffeurs}")
    c2.metric("Distance totale", f"{int(km_totaux)} km")
    c3.metric("Km moyen / chauffeur", f"{int(km_moyen_chauffeur)} km")

    

def afficher_stats_chauffeurs(postes, config_rh):
    """Affiche le récapitulatif par chauffeur (Amplitude, Travail, Pauses)."""
    st.subheader("👨‍✈️ Récapitulatif par Chauffeur")
    
    recap = []
    for c_id, tournees in postes.items():
        # Heure de départ de la toute première tournée
        debut_vacation = tournees[0][0]['heure']
        # Heure de retour de la toute dernière tournée
        fin_vacation = tournees[-1][-1]['heure']
        
        amplitude = fin_vacation - debut_vacation
        
        # Calcul du temps de travail effectif (somme des durées de chaque tournée)
        temps_travail = 0
        for trne in tournees:
            temps_travail += (trne[-1]['heure'] - trne[0]['heure'])
        
        recap.append({
            "Chauffeur": c_id,
            "Début": minutes_to_hhmm(debut_vacation),
            "Fin": minutes_to_hhmm(fin_vacation),
            "Amplitude": f"{amplitude // 60}h{amplitude % 60:02d}",
            "Travail effectif": f"{temps_travail // 60}h{temps_travail % 60:02d}",
            "Nb Tournées": len(tournees)
        })
    
    st.table(pd.DataFrame(recap))

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

def afficher_detail_itineraire(postes):
    """Affiche le déroulé chronologique pour chaque chauffeur."""
    st.subheader("📑 Détail des itinéraires")
    
    for c_id, tournees in postes.items():
        with st.expander(f"📋 Planning détaillé - {c_id}", expanded=False):
            data_rows = []
            for idx, trne in enumerate(tournees):
                for step in trne:
                    data_rows.append({
                        "Tournée": f"n°{idx+1}",
                        "Heure": minutes_to_hhmm(step['heure']),
                        "Site": step['site'],
                        "Action": "Dépôt" if step['site'] == "HLS" else "Collecte"
                    })
            
            df_plan = pd.DataFrame(data_rows)
            st.dataframe(df_plan, use_container_width=True, hide_index=True)
