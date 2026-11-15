


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r1_d6_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[0] < 0.360167f) {
              if (features[2] < 0.276552f) {
                  if (features[4] < -0.016338f) {
                      if (features[4] < -1.048745f) {
                          if (features[0] < -0.899795f) {
                              if (features[3] < -0.956436f) {
                                  return 0;
                              } else {
                                  return 0;
                              }
                          } else {
                              if (features[2] < -1.234414f) {
                                  return 0;
                              } else {
                                  return 2;
                              }
                          }
                      } else {
                          if (features[8] < 1.890260f) {
                              if (features[0] < -0.799786f) {
                                  return 2;
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[1] < -0.813666f) {
                                  return 1;
                              } else {
                                  return 0;
                              }
                          }
                      }
                  } else {
                      if (features[8] < 0.037818f) {
                          if (features[0] < -0.882509f) {
                              if (features[8] < -0.269725f) {
                                  return 2;
                              } else {
                                  return 0;
                              }
                          } else {
                              if (features[2] < 0.091719f) {
                                  return 0;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[1] < -0.618408f) {
                              if (features[8] < 2.954675f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[4] < 0.076498f) {
                                  return 0;
                              } else {
                                  return 0;
                              }
                          }
                      }
                  }
              } else {
                  if (features[6] < -0.643153f) {
                      if (features[2] < 0.508438f) {
                          if (features[4] < -0.326841f) {
                              return 2;
                          } else {
                              if (features[1] < 0.422159f) {
                                  return 1;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[6] < -0.702053f) {
                              if (features[1] < 0.708169f) {
                                  return 2;
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[2] < 1.052629f) {
                                  return 2;
                              } else {
                                  return 2;
                              }
                          }
                      }
                  } else {
                      if (features[6] < -0.522469f) {
                          if (features[3] < 0.616648f) {
                              if (features[2] < 0.690544f) {
                                  return 1;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[7] < -0.000333f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[6] < 0.657870f) {
                              if (features[7] < -0.552306f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              return 0;
                          }
                      }
                  }
              }
          } else {
              if (features[1] < 2.077469f) {
                  if (features[4] < 1.813361f) {
                      if (features[2] < 0.070358f) {
                          if (features[0] < 1.156729f) {
                              if (features[7] < 0.150645f) {
                                  return 1;
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[8] < 1.582886f) {
                                  return 1;
                              } else {
                                  return 2;
                              }
                          }
                      } else {
                          if (features[7] < -1.757946f) {
                              if (features[5] < 1.485548f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              if (features[5] < 0.098738f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          }
                      }
                  } else {
                      if (features[5] < 0.200441f) {
                          if (features[8] < 0.259438f) {
                              return 1;
                          } else {
                              if (features[9] < 0.375262f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          }
                      } else {
                          if (features[5] < 0.498666f) {
                              if (features[0] < 1.165902f) {
                                  return 1;
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[2] < 2.020089f) {
                                  return 1;
                              } else {
                                  return 1;
                              }
                          }
                      }
                  }
              } else {
                  if (features[5] < 0.743111f) {
                      if (features[8] < -0.333027f) {
                          return 1;
                      } else {
                          if (features[8] < -0.127346f) {
                              if (features[9] < -0.019562f) {
                                  return 2;
                              } else {
                                  return 1;
                              }
                          } else {
                              return 2;
                          }
                      }
                  } else {
                      if (features[0] < 1.222065f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  }
              }
          }
        }
        

int32_t dt_r1_d6_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[3] = {0,};
        int32_t _class = -1;

        _class = dt_r1_d6_fp32_tree_0(features, features_length); votes[_class] += 1;
    
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
    

int dt_r1_d6_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r1_d6_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    