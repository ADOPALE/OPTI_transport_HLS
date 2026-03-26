import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

def afficher_tableau_bord_global(planning):
    """Affiche les KPI de haut niveau (Besoin max, moyenne, tableau hebdo)."""
    st.header("1. Dimensionnement RH Global")
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    nb_postes = [len(planning[f"Quantité {j}"]['chauffeurs']) for j in jours_nom]
    
    col1, col2 = st.columns(2)
    col1.metric("Besoin Max (ETP)", f"{max(nb_postes)} chauffeurs")
    col2.metric("Moyenne Semaine", f"{np.mean(nb_postes):.1f}")

    # Tableau récapitulatif
    df_recap = pd.DataFrame({"Jour": jours_nom, "Nombre de postes (7h30)": nb_postes})
    st.table(df_recap)

def afficher_analyse_operationnelle(planning, df_vehicules, df_contenants):
    """Gère l'interactivité : Choix du jour -> Chauffeur -> Tournée."""
    st.header("2. Analyse Opérationnelle Détaillée")
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    jour_selected = st.selectbox("📅 Choisir un jour", jours_nom)
    key_jour = f"Quantité {jour_selected}"
    
    data_jour = planning[key_jour]
    
    if not data_jour['chauffeurs']:
        st.warning("Aucune activité ce jour-là.")
        return

    # --- SÉLECTION DU CHAUFFEUR ---
    labels_chauffeurs = [f"Chauffeur {i+1} ({c['type_vehicule']})" for i, c in enumerate(data_jour['chauffeurs'])]
    chauffeur_sel = st.selectbox("🚚 Choisir un chauffeur / véhicule", labels_chauffeurs)
    idx_c = labels_chauffeurs.index(chauffeur_sel)
    c_data = data_jour['chauffeurs'][idx_c]

    # --- TIMELINE DU CHAUFFEUR ---
    st.subheader(f"Planning : {chauffeur_sel}")
    fig_gantt = generer_graphique_gantt(c_data['tournees'])
    st.plotly_chart(fig_gantt, use_container_width=True)

    # Métriques du chauffeur
    m1, m2, m3 = st.columns(3)
    m1.metric("Amplitude", f"{int(c_data['t_total'])} min")
    m2.metric("Dont Roulage", f"{int(c_data['t_roulage'])} min")
    m3.metric("Dont Manut", f"{int(c_data['t_manut'])} min")

    # --- DÉTAIL D'UNE TOURNÉE & BIN PACKING ---
    labels_rot = [f"Tournée {i+1} : {r['label']}" for i, r in enumerate(c_data['tournees'])]
    rot_sel = st.selectbox("📦 Détail du chargement", labels_rot)
    idx_r = labels_rot.index(rot_sel)
    r_data = c_data['tournees'][idx_r]

    st.info(f"Contenu : **{r_data['contenant_qte']} {r_data['contenant_type']}**")
    
    # Import dynamique de la fonction de dessin (pour éviter les cercles d'import)
    from simul_flux import generer_visuel_bin_packing
    fig_bin = generer_visuel_bin_packing(
        r_data['contenant_type'], 
        r_data['contenant_qte'], 
        c_data['type_vehicule'], 
        df_vehicules, 
        df_contenants
    )
    st.plotly_chart(fig_bin, use_container_width=True)

def generer_graphique_gantt(tournees):
    """Crée une timeline propre avec Plotly."""
    df_list = []
    for i, t in enumerate(tournees):
        # On crée des dates fictives pour que Plotly Gantt fonctionne (base 01/01/2024)
        start = pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=t['debut'])
        end = pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=t['fin'])
        
        df_list.append(dict(
            Task=f"Tournée {i+1}",
            Start=start,
            Finish=end,
            Resource=t['label']
        ))
    
    fig = px.timeline(df_list, x_start="Start", x_end="Finish", y="Task", color="Resource",
                      title="Chronologie de la journée (HH:MM)")
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(showlegend=False)
    return fig
