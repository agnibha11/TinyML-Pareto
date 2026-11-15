


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r2_d6_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[0] < 0.360167f) {
              if (features[19] < 0.142439f) {
                  if (features[22] < 0.774566f) {
                      if (features[12] < -0.737534f) {
                          if (features[0] < -0.897676f) {
                              if (features[4] < -0.722188f) {
                                  return 0;
                              } else {
                                  return 0;
                              }
                          } else {
                              if (features[16] < 0.175378f) {
                                  return 0;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[13] < 0.516275f) {
                              if (features[8] < 1.913548f) {
                                  return 2;
                              } else {
                                  return 0;
                              }
                          } else {
                              if (features[13] < 0.890142f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          }
                      }
                  } else {
                      if (features[10] < -0.705503f) {
                          if (features[2] < 0.080671f) {
                              if (features[12] < -0.015594f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          } else {
                              return 1;
                          }
                      } else {
                          if (features[8] < -0.264080f) {
                              if (features[15] < 0.275943f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          } else {
                              if (features[2] < -0.650871f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          }
                      }
                  }
              } else {
                  if (features[1] < 0.120115f) {
                      if (features[16] < 1.000993f) {
                          if (features[14] < -0.356383f) {
                              if (features[13] < -0.049088f) {
                                  return 1;
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[6] < 0.321805f) {
                                  return 2;
                              } else {
                                  return 0;
                              }
                          }
                      } else {
                          if (features[22] < 0.744920f) {
                              if (features[0] < -0.685521f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[15] < 0.119435f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          }
                      }
                  } else {
                      if (features[6] < -0.641707f) {
                          if (features[17] < 1.394867f) {
                              if (features[15] < 0.587113f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[18] < 1.485028f) {
                                  return 1;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[8] < 0.659493f) {
                              if (features[6] < -0.522469f) {
                                  return 1;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[16] < 0.599364f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          }
                      }
                  }
              }
          } else {
              if (features[12] < 1.505715f) {
                  if (features[10] < 0.634197f) {
                      if (features[11] < -0.508017f) {
                          if (features[18] < -0.271468f) {
                              if (features[21] < -0.631431f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[13] < -0.914630f) {
                                  return 1;
                              } else {
                                  return 2;
                              }
                          }
                      } else {
                          if (features[13] < 0.049588f) {
                              if (features[1] < -1.245075f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[11] < -0.171582f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          }
                      }
                  } else {
                      if (features[10] < 0.691124f) {
                          if (features[18] < -0.214220f) {
                              if (features[1] < -1.227519f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[11] < -0.610926f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[14] < 1.625247f) {
                              if (features[1] < -1.245392f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              return 2;
                          }
                      }
                  }
              } else {
                  if (features[19] < 1.848935f) {
                      if (features[5] < 1.593431f) {
                          if (features[10] < 1.446789f) {
                              if (features[19] < 1.183174f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[5] < 1.032220f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[21] < -0.384370f) {
                              return 2;
                          } else {
                              return 1;
                          }
                      }
                  } else {
                      if (features[10] < 1.746989f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  }
              }
          }
        }
        

int32_t dt_r2_d6_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[3] = {0,};
        int32_t _class = -1;

        _class = dt_r2_d6_fp32_tree_0(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<3; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int dt_r2_d6_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r2_d6_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    