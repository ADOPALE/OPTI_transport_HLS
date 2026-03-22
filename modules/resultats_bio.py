import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta
import plotly.express as px
import folium
from streamlit_folium import st_folium

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

    st.plotly_chart(fig, use_container_width=True)


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
                        passages_data.append({
                            "Site": site,
                            "Heure": point['heure'],
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
        x="Heure",
        y="Site",
        color="Véhicule",
        symbol="Véhicule",
        hover_data={"Heure": False, "Site": True},
        title="Répartition des collectes sur la journée"
    )

    # Personnalisation de l'affichage
    fig_sites.update_traces(marker=dict(size=12, opacity=0.8))
    
    fig_sites.update_layout(
        xaxis=dict(
            title="Heure de passage",
            tickvals=list(range(300, 1321, 60)),
            ticktext=[f"{h//60}h" for h in range(300, 1321, 60)],
            range=[300, 1320]
        ),
        yaxis=dict(title=None, categoryorder='category ascending'),
        height=400 + (len(df_passages["Site"].unique()) * 20),
        margin=dict(l=10, r=10, t=50, b=50)
    )

    # Ajout d'une info-bulle personnalisée pour lire l'heure HH:MM
    fig_sites.update_traces(
        hovertemplate="<b>%{y}</b><br>Passage à %{customdata}h<extra></extra>",
        customdata=[f"{int(h//60):02d}:{int(h%60):02d}" for h in df_passages["Heure"]]
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




def afficher_detail_itineraire(v_id, vacations, df_coords):
    """
    Affiche le menu de sélection de la tournée et son déroulé précis.
    """
    # 1. Création de la liste plate des tournées pour le menu
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
    col_sel, col_vide = st.columns([1, 1])
    with col_sel:
        selection = st.selectbox("Choisir une tournée précise", tous_les_passages, format_func=lambda x: x["label"])

    if selection:
        trne_data = selection["data"]
        
        # 2. Tableau des passages
        st.write(f"#### ⏱️ Journal de bord : {selection['label']}")
        
        tableau = []
        for i, p in enumerate(trne_data):
            h_str = f"{int(p['heure']//60):02d}:{int(p['heure']%60):02d}"
            type_arret = "🏁 Dépôt (Retour)" if i == len(trne_data)-1 else ("🚀 Départ HLS" if i == 0 else "🏥 Collecte")
            tableau.append({
                "Ordre": i + 1,
                "Site": p['site'],
                "Heure de passage": h_str,
                "Type": type_arret
            })
        
        st.table(pd.DataFrame(tableau).set_index("Ordre"))

        # 3. Carte Géographique (si coordonnées disponibles)
        if df_coords is not None:
            st.write("#### 🗺️ Itinéraire géographique")
            
            # Nettoyage index coordonnées
            df_c = df_coords.copy()
            df_c.index = df_c['site'].astype(str).str.strip().str.upper()

            # Centre de la carte sur le premier point (HLS)
            m = folium.Map(location=[df_c.iloc[0]['lat'], df_c.iloc[0]['lon']], zoom_start=12)
            
            points_gps = []
            for i, p in enumerate(trne_data):
                nom_site = p['site'].upper()
                if nom_site in df_c.index:
                    lat, lon = df_c.loc[nom_site, 'lat'], df_c.loc[nom_site, 'lon']
                    points_gps.append([lat, lon])
                    
                    # Marqueur
                    icon_color = 'red' if i == 0 or i == len(trne_data)-1 else 'blue'
                    folium.Marker(
                        [lat, lon], 
                        popup=f"{i+1}. {nom_site}",
                        tooltip=f"{nom_site}",
                        icon=folium.Icon(color=icon_color, icon='info-sign')
                    ).add_to(m)

            # Dessin de la ligne de trajet
            if len(points_gps) > 1:
                folium.PolyLine(points_gps, color="blue", weight=2.5, opacity=0.8).add_to(m)

            st_folium(m, width=700, height=400)


