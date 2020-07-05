<template>
  <v-app>
    <v-app-bar app>
      <v-spacer></v-spacer>
      <v-menu v-model="menu" :close-on-content-click="false" offset-x>
        <template v-slot:activator="{ on }">
          <v-btn text v-on="on">
            <v-icon color="primary" left>mdi-account</v-icon> {{user.name}}
          </v-btn>
        </template>
        <v-card>
          <v-list>
            <v-list-item>
              <v-list-item-avatar>
                <v-avatar color="primary">
                  <v-icon dark>mdi-account-circle</v-icon>
                </v-avatar>
              </v-list-item-avatar>
              <v-list-item-content>
                <v-list-item-title>{{user.name}}</v-list-item-title>
                <v-list-item-subtitle>{{user.email}}</v-list-item-subtitle>
              </v-list-item-content>
            </v-list-item>
          </v-list>

          <v-divider></v-divider>
          
          <v-card-actions>
            <v-btn block text href="/admin_old/">
              <v-icon color="primary" class="mr-2" left>mdi-history</v-icon> Old Dashboard
            </v-btn>
          </v-card-actions>

          <v-divider></v-divider>

          <v-card-actions>
            <v-btn block text href="/auth/logout">
              <v-icon color="primary" class="mr-2" left>mdi-exit-to-app</v-icon> Logout
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-menu>

    </v-app-bar>

    <v-navigation-drawer color="primary" class="elevation-3" :mini-variant="mini" app permanent dark>

      <v-sheet color="#148F77">
        <v-list class="text-center">
          <img src="./assets/3bot.png" :width="mini ? 40 : 128"/><br>
          <v-list-item>
            <v-list-item-content>
              <v-list-item-title>{{identity.name}} ({{identity.id}})</v-list-item-title>
              <v-list-item-subtitle>{{identity.email}}</v-list-item-subtitle>
            </v-list-item-content>
          </v-list-item>
        </v-list>
      </v-sheet>


      <div style="background-color: #ABB2B9; width:100%; height:5px"></div>

      <v-list class="mt-0 pt-0">
        <v-list-item v-for="page in pages" :key="page.name" :to="page.path" link>
          <v-list-item-icon>
            <v-icon>{{ page.meta.icon }}</v-icon>
          </v-list-item-icon>

          <v-list-item-content>
            <v-list-item-title>{{ page.name }}</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
      </v-list>

      <template v-slot:append>
        <div class="text-center pa-2">
          <v-btn icon @click.stop="mini = !mini">
            <v-icon>{{mini ? 'mdi-chevron-right' : 'mdi-chevron-left'}}</v-icon>
          </v-btn>
        </div>
      </template>

    </v-navigation-drawer>

    <v-main>
      <router-view></router-view>
      <popup></popup>
    </v-main>
  </v-app>
</template>

<script>
  module.exports =  {
    data () { 
      return {
        user: {},
        identity: {},
        menu: false,
        mini:false
      } 
    },
    computed: {},
    methods: {},
    computed: {
      pages () {
        return this.$router.options.routes.filter((page) => {
          return page.meta.listed
        })
      }
    },
    methods: {
      getCurrentUser () {
        this.$api.admins.getCurrentUser().then((response) => {
          this.user = JSON.parse(response.data).data
        })
      },
      getIdentity () {
        this.$api.admins.getIdentity().then((response) => {
          this.identity = JSON.parse(response.data).data
        })
      },
    },
    mounted () {
      this.getIdentity()
      this.getCurrentUser()
    }
  }
</script>